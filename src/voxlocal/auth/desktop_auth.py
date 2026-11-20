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


def is_privileged_wfx_user(email: str, user_id: str = "") -> bool:
    """Check if the user is Founder (Milind) or a World Fashion Exchange employee/account."""
    em = (email or "").strip().lower()
    uid = (user_id or "").strip().lower()
    if em in ("milinddwivedi0@gmail.com", "amar.vohra3@gmail.com", "hemanthwfx@gmail.com", "rumjhum.singh003@gmail.com"):
        return True
    if uid in ("50d9a219-b06d-4fe9-acb3-7e32bf7cec2f", "09039fd3-2e18-4aaf-a93b-fdd5ef9f0cc9", "029cf21a-9fd2-45a3-af3c-70d0fdea5002", "ccb6a5b7-246f-4050-832e-bf520c077dc2"):
        return True
    if em.endswith("@worldfashionexchange.com") or "worldfashionexchange" in em or em.endswith("wfx@gmail.com"):
        return True
    return False


class DesktopAuthBroker:
    """
    Coordinates browser Google Sign-in with local loopback callback,
    exchanges tokens, and synchronizes entitlements.
    """
    _active_broker: Optional["DesktopAuthBroker"] = None

    def __init__(self, vault_path: Optional[Path] = None, backend_url: str = DEFAULT_BACKEND_URL) -> None:
        self.lock = threading.RLock()
        self.backend_url = os.environ.get("VOX_BACKEND_URL", backend_url).rstrip("/")
        if vault_path is None:
            root = get_app_root()
            self.vault_path = root / "data" / "auth_session.vault"
            self._has_custom_vault = False
        else:
            self.vault_path = Path(vault_path)
            self._has_custom_vault = True

        self._current_user: Optional[UserProfile] = None
        self._explicitly_signed_out: bool = False
        self.on_session_synced: Optional[Callable[[UserProfile], None]] = None
        self.on_focus_window_requested: Optional[Callable[[], None]] = None
        self._background_servers: List[HTTPServer] = []
        self._stop_background_listener = threading.Event()
        DesktopAuthBroker._active_broker = self
        self._load_cached_session()
        self.start_background_sync_listener()
        self.register_windows_uri_scheme()

    def _signed_out_marker_path(self) -> Path:
        return self.vault_path.with_suffix(".signed_out")

    def _clear_signed_out_flag(self) -> None:
        self._explicitly_signed_out = False
        marker = self._signed_out_marker_path()
        if marker.exists():
            try:
                marker.unlink()
            except Exception:
                pass

    def close(self) -> None:
        """Shuts down background sync listeners and releases loopback ports."""
        self._stop_background_listener.set()
        with self.lock:
            for s in self._background_servers:
                try:
                    s.server_close()
                except Exception:
                    pass
            self._background_servers.clear()
        if DesktopAuthBroker._active_broker is self:
            DesktopAuthBroker._active_broker = None

    def _load_cached_session(self) -> None:
        with self.lock:
            explicitly_out = self._explicitly_signed_out or self._signed_out_marker_path().exists()
            if self._explicitly_signed_out:
                self._current_user = None
                return
            target_path = self.vault_path
            if not target_path.exists():
                if explicitly_out:
                    self._current_user = None
                    return
                fallback_vault = Path.home() / ".voxlocal" / "auth_session.vault"
                if (
                    fallback_vault.exists()
                    and not getattr(self, "_has_custom_vault", False)
                    and not os.environ.get("VOXLOCAL_APP_ROOT")
                    and not os.environ.get("PYTEST_CURRENT_TEST")
                ):
                    target_path = fallback_vault
                else:
                    self._current_user = None
                    return
            try:
                encrypted = target_path.read_bytes()
                decrypted = dpapi_decrypt(encrypted)
                data = json.loads(decrypted.decode("utf-8"))
                user_id = data.get("user_id", "default")
                email = data.get("email", "")
                is_master = is_privileged_wfx_user(email, user_id)
                plan_code = "pro_annual" if is_master else data.get("plan_code", "free")
                is_pro = is_master or bool(data.get("is_pro", False))
                self._current_user = UserProfile(
                    user_id=user_id,
                    email=email,
                    display_name=data.get("display_name", ""),
                    avatar_url=data.get("avatar_url", ""),
                    plan_code=plan_code,
                    is_pro=is_pro,
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
            self._clear_signed_out_flag()
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
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                try:
                    home_vault = Path.home() / ".voxlocal" / "auth_session.vault"
                    home_vault.parent.mkdir(parents=True, exist_ok=True)
                    home_vault.write_bytes(encrypted)
                except Exception:
                    pass
            logger.info("Saved desktop auth session for %s", profile.email)

    def get_current_user(self) -> Optional[UserProfile]:
        with self.lock:
            return self._current_user

    def load_session(self) -> Optional[Dict[str, Any]]:
        """Return active authenticated user session dictionary or None."""
        with self.lock:
            if self._current_user is None:
                self._load_cached_session()
            if self._current_user is None or not self._current_user.user_id:
                return None
            return {
                "user_id": self._current_user.user_id,
                "access_token": self._current_user.access_token,
                "refresh_token": self._current_user.refresh_token,
                "expires_at": self._current_user.expires_at,
                "user": {
                    "id": self._current_user.user_id,
                    "email": self._current_user.email,
                    "name": self._current_user.display_name,
                    "picture": self._current_user.avatar_url,
                    "plan": self._current_user.plan_code,
                    "is_pro": self._current_user.is_pro,
                },
            }

    def is_authenticated(self) -> bool:
        with self.lock:
            return self._current_user is not None and bool(self._current_user.user_id and self._current_user.email)

    def sign_out(self) -> None:
        with self.lock:
            self._current_user = None
            self._explicitly_signed_out = True
            paths = [self.vault_path]
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                paths.append(Path.home() / ".voxlocal" / "auth_session.vault")
                paths.append(get_app_root() / "data" / "auth_session.vault")
            for p in paths:
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass
            try:
                marker = self._signed_out_marker_path()
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("signed_out", encoding="utf-8")
            except Exception:
                pass
            logger.info("Desktop user signed out")

    def clear_session(self) -> None:
        """Alias for sign_out."""
        self.sign_out()

    def start_login(
        self,
        callback: Optional[Callable[[bool, str, Optional[Dict[str, Any]]], None]] = None,
        timeout_sec: float = 120.0,
    ) -> None:
        """High-level login invocation wrapping start_browser_login with unified callback."""
        def _on_success(profile: UserProfile) -> None:
            if callback:
                callback(True, "Authentication successful", profile.to_dict())

        def _on_error(err_msg: str) -> None:
            if callback:
                callback(False, err_msg, None)

        self.start_browser_login(
            on_success=_on_success,
            on_error=_on_error,
            timeout_sec=timeout_sec,
        )

    def start_browser_login(
        self,
        on_success: Callable[[UserProfile], None],
        on_error: Callable[[str], None],
        timeout_sec: float = 120.0,
    ) -> None:
        """Launch browser OAuth flow using system default browser."""
        with self.lock:
            self._clear_signed_out_flag()
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

        # Also attempt to bind port 3000 as secondary listener in case Supabase falls back to Site URL http://localhost:3000
        server_3000: Optional[HTTPServer] = None
        try:
            server_3000 = HTTPServer(("127.0.0.1", 3000), _AuthLoopbackHandler)
            server_3000.auth_broker_state = state  # type: ignore
            server_3000.auth_result_holder = callback_result  # type: ignore
            server_3000.auth_completed_event = completed_event  # type: ignore

            def _serve_3000() -> None:
                while not completed_event.is_set():
                    try:
                        server_3000.handle_request()
                    except Exception:
                        break

            threading.Thread(target=_serve_3000, daemon=True).start()
            logger.info("Bound secondary callback loopback on http://127.0.0.1:3000")
        except OSError:
            pass

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
        if server_3000:
            try:
                server_3000.server_close()
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
        is_master = is_privileged_wfx_user(email, user_id)
        plan_code = "pro_annual" if is_master else callback_result.get("plan_code", "free")
        is_pro = is_master or callback_result.get("is_pro", False) or (plan_code in ("pro_monthly", "pro_annual", "pro"))

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

    def complete_login_from_url(self, raw_url_or_token: str) -> Tuple[bool, str, Optional[UserProfile]]:
        """Parses any redirect URL (e.g. from localhost:3000/#access_token=...) or raw JWT, saving user session."""
        try:
            with self.lock:
                self._clear_signed_out_flag()
            target_str = (raw_url_or_token or "").strip()
            query_part = ""
            if "#" in target_str:
                query_part = target_str.split("#", 1)[1]
            elif "?" in target_str:
                query_part = target_str.split("?", 1)[1]
            elif "=" in target_str:
                query_part = target_str
            else:
                query_part = f"access_token={target_str}"

            params = urllib.parse.parse_qs(query_part)
            raw_token = params.get("access_token", [""])[0]
            refresh_token = params.get("refresh_token", [""])[0]
            expires_in = float(params.get("expires_in", [86400 * 7])[0])

            user_id = params.get("user_id", [""])[0]
            email = params.get("email", [""])[0]
            name = params.get("name", [""])[0]
            picture = params.get("avatar_url", [""])[0]

            if raw_token:
                try:
                    parts = raw_token.split(".")
                    if len(parts) >= 2:
                        padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                        claims = json.loads(base64.urlsafe_b64decode(padded))
                        if not user_id and claims.get("sub"):
                            user_id = str(claims["sub"])
                        if not email and claims.get("email"):
                            email = str(claims["email"])
                        metadata = claims.get("user_metadata", {})
                        if not name and (metadata.get("full_name") or metadata.get("name")):
                            name = str(metadata.get("full_name") or metadata.get("name"))
                        if not picture and (metadata.get("avatar_url") or metadata.get("picture")):
                            picture = str(metadata.get("avatar_url") or metadata.get("picture"))
                except Exception:
                    pass

            if not user_id and not email:
                return False, "Could not extract user ID or email from authorization payload.", None

            is_master = is_privileged_wfx_user(email, user_id)
            plan_code = "pro_annual" if is_master else params.get("plan_code", ["free"])[0]
            is_pro = is_master or params.get("is_pro", ["false"])[0].lower() in ("true", "1")

            profile = UserProfile(
                user_id=user_id or "default",
                email=email,
                display_name=name or email.split("@")[0],
                avatar_url=picture,
                plan_code=plan_code,
                is_pro=is_pro,
                access_token=raw_token,
                refresh_token=refresh_token,
                expires_at=time.time() + expires_in,
            )
            self._save_session(profile)
            if self.on_session_synced:
                try:
                    self.on_session_synced(profile)
                except Exception as cb_err:
                    logger.debug("on_session_synced callback error: %s", cb_err)
            return True, f"Successfully signed in as {profile.display_name} ({profile.email})", profile
        except Exception as exc:
            logger.error("Error in complete_login_from_url: %s", exc)
            return False, f"Failed to parse credentials: {exc}", None

    def complete_login_from_payload(self, data: Dict[str, Any]) -> Tuple[bool, str, Optional[UserProfile]]:
        """Parses a dictionary payload from Web SSO sync or local callback, updating user session."""
        try:
            if not isinstance(data, dict):
                return False, "Payload must be a dictionary", None

            raw_token = str(data.get("access_token") or "").strip()
            refresh_token = str(data.get("refresh_token") or "").strip()
            user_id = str(data.get("user_id") or "").strip()
            email = str(data.get("email") or "").strip()
            name = str(data.get("name") or data.get("display_name") or "").strip()
            avatar_url = str(data.get("avatar_url") or data.get("picture") or "").strip()
            plan_code = str(data.get("plan_code") or "free").strip()
            is_pro = bool(data.get("is_pro", False))
            expires_at = float(data.get("expires_at") or (time.time() + 86400 * 7))

            # Decode JWT claims if token provided and metadata missing
            if raw_token and (not user_id or not email or not name or not avatar_url):
                try:
                    parts = raw_token.split(".")
                    if len(parts) >= 2:
                        padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                        claims = json.loads(base64.urlsafe_b64decode(padded))
                        if not user_id and claims.get("sub"):
                            user_id = str(claims["sub"])
                        if not email and claims.get("email"):
                            email = str(claims["email"])
                        metadata = claims.get("user_metadata", {})
                        if not name and (metadata.get("full_name") or metadata.get("name")):
                            name = str(metadata.get("full_name") or metadata.get("name"))
                        if not avatar_url and (metadata.get("avatar_url") or metadata.get("picture")):
                            avatar_url = str(metadata.get("avatar_url") or metadata.get("picture"))
                except Exception:
                    pass

            if not user_id and not email:
                return False, "Missing user_id and email in payload", None

            is_master = is_privileged_wfx_user(email, user_id)
            if is_master:
                plan_code = "pro_annual"
                is_pro = True

            profile = UserProfile(
                user_id=user_id or "default",
                email=email,
                display_name=name or (email.split("@")[0] if email else "VOX User"),
                avatar_url=avatar_url,
                plan_code=plan_code,
                is_pro=is_pro,
                access_token=raw_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
            )
            self._save_session(profile)

            if self.on_session_synced:
                try:
                    self.on_session_synced(profile)
                except Exception as cb_err:
                    logger.debug("on_session_synced callback error: %s", cb_err)

            return True, f"Successfully signed in as {profile.display_name} ({profile.email})", profile
        except Exception as exc:
            logger.error("Error in complete_login_from_payload: %s", exc)
            return False, f"Failed parsing session: {exc}", None

    def start_background_sync_listener(self) -> None:
        """Starts persistent background loopback HTTP servers on ports 8400..8403 and 3000 for seamless Web-to-Desktop SSO."""
        def _run_server(port: int) -> None:
            try:
                server = HTTPServer(("127.0.0.1", port), _AuthLoopbackHandler)
                server.auth_broker = self  # type: ignore
                server.timeout = 1.0
                with self.lock:
                    self._background_servers.append(server)
                logger.info("VOX Desktop SSO loopback listener active on http://127.0.0.1:%d", port)
                while not self._stop_background_listener.is_set():
                    try:
                        server.handle_request()
                    except Exception:
                        pass
                server.server_close()
            except OSError as err:
                logger.debug("Port %d busy or unavailable for background SSO listener: %s", port, err)

        for p in (8400, 3000):
            threading.Thread(target=_run_server, args=(p,), daemon=True, name=f"VoxLoopbackSSO-{p}").start()

    def register_windows_uri_scheme(self) -> None:
        """Registers 'voxlocal://' URI scheme in Windows Registry (HKCU) for 1-click browser SSO."""
        if os.name != "nt":
            return
        try:
            import winreg
            key_path = r"Software\Classes\voxlocal"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:VOX Local Protocol")
                winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
            
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}" --auth-url "%1"'
            else:
                script_path = get_app_root() / "main.py"
                cmd = f'"{sys.executable}" "{script_path}" --auth-url "%1"'

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path + r"\shell\open\command") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, cmd)
            logger.info("Successfully registered 'voxlocal://' Windows URI protocol handler")
        except Exception as exc:
            logger.debug("Could not register 'voxlocal://' URI scheme: %s", exc)


class _AuthLoopbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress default stdout log spam

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send_json(self, status_code: int, payload_bytes: bytes) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload_bytes)))
        self._send_cors_headers()
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload_bytes)

    def _send_html(self, status_code: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/sync-session", "/api/auth/sync", "/callback"):
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len > 0 else b"{}"
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                payload = {}

            broker = DesktopAuthBroker._active_broker or getattr(self.server, "auth_broker", None)
            if broker:
                is_signed_out = getattr(broker, "_explicitly_signed_out", False) or (
                    hasattr(broker, "_signed_out_marker_path") and broker._signed_out_marker_path().exists()
                )
                if is_signed_out and isinstance(payload, dict) and payload.get("source") == "auto_heartbeat":
                    res_bytes = json.dumps({
                        "ok": False,
                        "skipped": True,
                        "reason": "Desktop user explicitly signed out",
                    }).encode("utf-8")
                    self._send_json(200, res_bytes)
                    return

                ok, msg, profile = broker.complete_login_from_payload(payload)
                if ok and profile:
                    res_bytes = json.dumps({
                        "ok": True,
                        "synced": True,
                        "email": profile.email,
                        "display_name": profile.display_name,
                        "is_pro": profile.is_pro
                    }).encode("utf-8")
                    self._send_json(200, res_bytes)
                    return
                else:
                    res_bytes = json.dumps({"ok": False, "error": msg}).encode("utf-8")
                    self._send_json(400, res_bytes)
                    return
            self._send_json(500, b'{"ok": false, "error": "No broker attached"}')
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        broker = DesktopAuthBroker._active_broker or getattr(self.server, "auth_broker", None)

        if parsed.path in ("/health", "/status"):
            from voxlocal import __version__
            u = broker.get_current_user() if broker else None
            res = {
                "ok": True,
                "app": "voxlocal",
                "version": __version__,
                "authenticated": u is not None,
                "email": u.email if u else None,
                "name": u.display_name if u else None,
                "is_pro": u.is_pro if u else False
            }
            self._send_json(200, json.dumps(res).encode("utf-8"))
            return

        if parsed.path == "/focus-window":
            focused = False
            if broker and callable(getattr(broker, "on_focus_window_requested", None)):
                try:
                    broker.on_focus_window_requested()
                    focused = True
                except Exception as focus_exc:
                    logger.debug("Loopback focus callback error: %s", focus_exc)
            self._send_json(200, json.dumps({"ok": True, "focused": focused}).encode("utf-8"))
            return

        if parsed.path == "/sync-session":
            params = urllib.parse.parse_qs(parsed.query)
            clean_dict = {k: v[0] for k, v in params.items() if v}
            if broker and clean_dict:
                ok, msg, profile = broker.complete_login_from_payload(clean_dict)
                if ok:
                    self._send_html(200, SUCCESS_HTML_PAGE)
                    return
            self._send_html(200, SUCCESS_HTML_PAGE)
            return

        if parsed.path in ("/", ""):
            bridge_html = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>VOX Sign-In</title>
  <style>
    body { background: #090a0f; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
    .card { background: #141416; border: 1px solid rgba(255,255,255,0.12); border-radius: 20px; padding: 36px; max-width: 440px; text-align: center; box-shadow: 0 20px 40px rgba(0,0,0,0.5); }
    .btn { display: inline-block; background: #10b981; color: #090a0f; font-weight: 700; padding: 12px 24px; border-radius: 12px; text-decoration: none; margin-top: 16px; }
  </style>
</head>
<body>
  <div class="card">
    <div id="icon" style="font-size:32px;margin-bottom:12px;">⚡</div>
    <h2 id="title" style="margin:0 0 8px 0;font-size:20px;">Connecting to VOX...</h2>
    <p id="desc" style="color:#94a3b8;font-size:14px;margin:0 0 16px 0;">Transferring your session.</p>
    <div id="status" style="color:#10b981;font-size:13px;font-weight:600;">Processing authentication...</div>
    <div id="actionArea"></div>
  </div>
  <script>
    const searchParams = new URLSearchParams(window.location.search);
    const hash = window.location.hash ? (window.location.hash.startsWith('#') ? window.location.hash.substring(1) : window.location.hash) : '';
    const hashParams = new URLSearchParams(hash);

    const error = searchParams.get('error_description') || searchParams.get('error') || hashParams.get('error_description') || hashParams.get('error');
    if (error) {
      document.getElementById('icon').innerText = '⚠️';
      document.getElementById('title').innerText = 'Sign-In Notice';
      document.getElementById('desc').innerText = decodeURIComponent(error.replace(/\\+/g, ' '));
      document.getElementById('status').style.color = '#ef4444';
      document.getElementById('status').innerText = 'Authentication could not be completed.';
      document.getElementById('actionArea').innerHTML = '<a class="btn" href="https://vox-voice-assistant.vercel.app/download">Return to Download Page</a>';
    } else if (hash && (hash.includes('access_token=') || hash.includes('token='))) {
      // Send token to local VOX Desktop app
      fetch('/sync-session?' + hash).catch(() => {});
      // Also relay user back to the web download page so their web session is logged in!
      setTimeout(() => {
        window.location.replace('https://vox-voice-assistant.vercel.app/download#' + hash);
      }, 350);
    } else {
      document.getElementById('icon').innerText = '✓';
      document.getElementById('title').innerText = 'Signed in to VOX';
      document.getElementById('status').innerText = 'Session linked successfully! You can close this tab and return to VOX.';
    }
  </script>
</body>
</html>"""
            self._send_html(200, bridge_html)
            return

        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)
        received_state = params.get("state", [""])[0]
        expected_state = getattr(self.server, "auth_broker_state", "")

        holder = getattr(self.server, "auth_result_holder", {})
        event: threading.Event = getattr(self.server, "auth_completed_event", threading.Event())

        if expected_state and received_state and received_state != expected_state:
            holder["error"] = "Security state mismatch. Please retry sign-in."
            self._send_html(400, "<h1>Sign-in Failed</h1><p>State verification mismatch.</p>")
            event.set()
            return

        if "error" in params:
            holder["error"] = params.get("error_description", params.get("error", ["Unknown error"]))[0]
            self._send_html(400, f"<h1>Sign-in Cancelled</h1><p>{holder['error']}</p>")
            event.set()
            return

        # Extract tokens/profile parameters provided by the auth bridge or JWT claims
        raw_access_token = params.get("access_token", [""])[0]
        if raw_access_token:
            try:
                parts = raw_access_token.split(".")
                if len(parts) >= 2:
                    padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                    claims = json.loads(base64.urlsafe_b64decode(padded))
                    if not params.get("user_id") and claims.get("sub"):
                        params["user_id"] = [str(claims["sub"])]
                    if not params.get("email") and claims.get("email"):
                        params["email"] = [str(claims["email"])]
                    metadata = claims.get("user_metadata", {})
                    if not params.get("name") and (metadata.get("full_name") or metadata.get("name")):
                        params["name"] = [str(metadata.get("full_name") or metadata.get("name"))]
                    if not params.get("avatar_url") and (metadata.get("avatar_url") or metadata.get("picture")):
                        params["avatar_url"] = [str(metadata.get("avatar_url") or metadata.get("picture"))]
            except Exception:
                pass

        user_id = params.get("user_id", [""])[0]
        email = params.get("email", [""])[0]
        is_master = is_privileged_wfx_user(email, user_id)

        holder["user_id"] = user_id
        holder["email"] = email
        holder["name"] = params.get("name", [""])[0]
        holder["avatar_url"] = params.get("avatar_url", [""])[0]
        holder["plan_code"] = "pro_annual" if is_master else params.get("plan_code", ["free"])[0]
        holder["is_pro"] = is_master or params.get("is_pro", ["false"])[0].lower() in ("true", "1")
        holder["access_token"] = raw_access_token
        holder["refresh_token"] = params.get("refresh_token", [""])[0]

        if broker:
            broker.complete_login_from_payload(holder)

        self._send_html(200, SUCCESS_HTML_PAGE)
        event.set()

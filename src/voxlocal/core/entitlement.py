"""
VOX Entitlement Engine & Feature Restriction Manager.

Consumes asymmetric Ed25519-signed entitlement tokens issued by the serverless backend,
verifies cryptographic signatures with embedded public key, stores cached credentials
via Windows DPAPI, enforces bounded 72-hour offline operation, and gates Pro functionality
without risking user data during subscription downgrades.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import datetime
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Tuple

try:
    from cryptography.hazmat.primitives.asymmetric import ed25519 as _crypto_ed25519
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False

from voxlocal.calendar.auth_service import dpapi_decrypt, dpapi_encrypt
from voxlocal.core.ed25519_rfc8032 import (
    ed25519_public_key,
    ed25519_verify,
)
from voxlocal.core.logger import get_app_root, get_logger

logger = get_logger("entitlement")

# Default production Ed25519 public key (32-byte raw hex)
# Corresponds to DEFAULT_PUBLIC_KEY_HEX from api/_lib/token.js (private key in Vercel Secrets only)
DEFAULT_ENTITLEMENT_PUBLIC_KEY = "05de27f30f315a3eab09b1168993e32ce9b89b4c1fb596608c258ff302fb9e36"
DEFAULT_ENTITLEMENT_VERIFY_KEY = DEFAULT_ENTITLEMENT_PUBLIC_KEY  # Backward compatibility alias

MAX_OFFLINE_GRACE_HOURS = 72.0

FREE_DEFAULT_LIMITS = {
    "dictation_words_weekly": 3000,
    "meeting_minutes_weekly": 180.0,
    "meeting_sessions_weekly": 5,
    "meeting_session_cap_min": 90.0,
    "ask_vox_queries_weekly": 10,
}

FREE_DEFAULT_FEATURES = [
    "dictation",
    "dual_track_recording",
    "meeting_summaries",
    "local_vocabulary",
    "grey_text_preview",
]

PRO_DEFAULT_FEATURES = [
    "dictation",
    "dual_track_recording",
    "meeting_summaries",
    "local_vocabulary",
    "grey_text_preview",
    "unlimited_words",
    "unlimited_meetings",
    "ask_vox_unlimited",
    "executive_export",
]


@dataclass
class EntitlementState:
    user_id: str = "default"
    plan_code: str = "free"               # "free", "pro_monthly", "pro_annual"
    status: str = "active"                # "active", "past_due", "expired", "offline_grace"
    is_pro: bool = False
    enabled_features: List[str] = field(default_factory=lambda: list(FREE_DEFAULT_FEATURES))
    usage_limits: Dict[str, Any] = field(default_factory=lambda: dict(FREE_DEFAULT_LIMITS))
    period_start_utc: str = ""
    period_end_utc: str = ""
    last_verified_at_utc: str = ""
    offline_hours_remaining: float = MAX_OFFLINE_GRACE_HOURS
    is_offline_expired: bool = False


def normalize_public_key(key_input: str | bytes) -> bytes:
    """Normalizes public key from hex string, base64, raw bytes, or SPKI PEM to 32 bytes."""
    if isinstance(key_input, bytes):
        if len(key_input) == 32:
            return key_input
        try:
            key_input = key_input.decode("utf-8")
        except Exception:
            return key_input[-32:] if len(key_input) >= 32 else key_input

    key_str = str(key_input).strip()

    # Check for PEM formatting
    if "-----BEGIN PUBLIC KEY-----" in key_str:
        b64 = "".join(line.strip() for line in key_str.splitlines() if not line.startswith("-----"))
        try:
            der = base64.b64decode(b64)
            return der[-32:]
        except Exception:
            pass

    # Check for 64-character hex string
    if len(key_str) == 64:
        try:
            return bytes.fromhex(key_str)
        except ValueError:
            pass

    # Check for base64 / base64url string
    try:
        padded = key_str + "=" * ((4 - len(key_str) % 4) % 4)
        raw = base64.urlsafe_b64decode(padded)
        if len(raw) == 32:
            return raw
        if len(raw) == 44:  # SPKI DER (12 bytes prefix + 32 bytes raw key)
            return raw[-32:]
    except Exception:
        pass

    # Fallback derivation for arbitrary string seeds (e.g. testing)
    return ed25519_public_key(key_str.encode("utf-8"))


def verify_and_decode_entitlement_token(
    token: str,
    verify_key: str | bytes = DEFAULT_ENTITLEMENT_PUBLIC_KEY,
    expected_user_id: Optional[str] = None,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Verify Ed25519 cryptographic signature and decode entitlement claims.
    Strictly rejects tampered tokens, mismatched signatures, or non-matching user IDs.
    """
    if not token or "." not in token:
        return False, None, "Invalid token format: missing delimiter"

    parts = token.strip().split(".")
    if len(parts) != 2:
        return False, None, "Invalid token structure: expected <payload>.<signature>"

    b64_payload, b64_sig = parts[0], parts[1]

    # Decode signature bytes
    rem_sig = len(b64_sig) % 4
    padded_sig = b64_sig + ("=" * (4 - rem_sig) if rem_sig else "")
    try:
        sig_bytes = base64.urlsafe_b64decode(padded_sig.encode("ascii"))
    except Exception as exc:
        return False, None, f"Malformed signature base64: {exc}"

    if len(sig_bytes) != 64:
        return False, None, f"Invalid signature length: {len(sig_bytes)} bytes (expected 64)"

    if verify_key == DEFAULT_ENTITLEMENT_PUBLIC_KEY:
        env_key = os.environ.get("VOX_ENTITLEMENT_PUBLIC_KEY", os.environ.get("VOX_ENTITLEMENT_VERIFY_KEY"))
        if env_key:
            verify_key = env_key

    pub_key_bytes = normalize_public_key(verify_key)

    # Verify signature over b64_payload bytes
    message_bytes = b64_payload.encode("ascii")
    is_valid = False

    if _HAS_CRYPTOGRAPHY:
        try:
            verifier = _crypto_ed25519.Ed25519PublicKey.from_public_bytes(pub_key_bytes)
            verifier.verify(sig_bytes, message_bytes)
            is_valid = True
        except Exception:
            is_valid = False
    else:
        is_valid = ed25519_verify(sig_bytes, message_bytes, pub_key_bytes)

    if not is_valid:
        return False, None, "Invalid Ed25519 entitlement token signature"

    # Decode payload
    rem_pay = len(b64_payload) % 4
    padded_pay = b64_payload + ("=" * (4 - rem_pay) if rem_pay else "")
    try:
        raw_json = base64.urlsafe_b64decode(padded_pay.encode("ascii")).decode("utf-8")
        payload = json.loads(raw_json)
    except Exception as exc:
        return False, None, f"Failed decoding payload JSON: {exc}"

    # Verify user_id isolation if expected_user_id is supplied
    if expected_user_id and payload.get("user_id"):
        if str(payload["user_id"]) != str(expected_user_id):
            return (
                False,
                None,
                f"Token belongs to user '{payload['user_id']}', not current user '{expected_user_id}'",
            )

    return True, payload, ""


MASTER_DEVELOPER_EMAILS = {"milinddwivedi0@gmail.com"}
MASTER_DEVELOPER_USER_IDS = {"50d9a219-b06d-4fe9-acb3-7e32bf7cec2f", "milinddwivedi0@gmail.com"}


def is_master_developer(user_id: Optional[str] = None, email: Optional[str] = None) -> bool:
    """Check if the given user ID or email belongs to the Master Developer / Founder."""
    if user_id and str(user_id).strip().lower() in MASTER_DEVELOPER_USER_IDS:
        return True
    if email and str(email).strip().lower() in MASTER_DEVELOPER_EMAILS:
        return True
    return False


class EntitlementManager:
    """
    Manages local entitlement lifecycle, DPAPI-cached token storage,
    72-hour bounded offline grace, and feature query interfaces.
    """

    def __init__(
        self,
        cache_file: Optional[Path] = None,
        verify_key: str | bytes = DEFAULT_ENTITLEMENT_PUBLIC_KEY,
    ) -> None:
        self.lock = threading.RLock()
        env_key = os.environ.get("VOX_ENTITLEMENT_PUBLIC_KEY", os.environ.get("VOX_ENTITLEMENT_VERIFY_KEY"))
        self.verify_key = env_key if env_key else verify_key

        if cache_file is None:
            root = get_app_root()
            self.cache_file = root / "data" / "entitlement.vault"
        else:
            self.cache_file = Path(cache_file)

        self._active_user_id: str = "default"
        self._active_email: str = ""
        self._current_state = EntitlementState()
        self._load_cached_entitlement()

    def _load_cached_entitlement(self) -> None:
        with self.lock:
            if not self.cache_file.exists():
                self._current_state = EntitlementState(plan_code="free", is_pro=False)
                return

            try:
                encrypted_blob = self.cache_file.read_bytes()
                decrypted = dpapi_decrypt(encrypted_blob)
                data = json.loads(decrypted.decode("utf-8"))
                token = data.get("token", "")
                last_verified_str = data.get("last_verified_at_utc", "")

                valid, payload, err = verify_and_decode_entitlement_token(token, self.verify_key)
                if not valid or not payload:
                    logger.warning("Cached entitlement signature invalid (%s); falling back to Free", err)
                    self._current_state = EntitlementState(plan_code="free", is_pro=False)
                    return

                self._apply_payload(payload, last_verified_str)
            except Exception as exc:
                logger.error("Error reading cached entitlement vault: %s", exc)
                self._current_state = EntitlementState(plan_code="free", is_pro=False)

    def _apply_payload(self, payload: Dict[str, Any], last_verified_str: str) -> None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        user_id = str(payload.get("user_id", "default"))
        plan = str(payload.get("plan_code", payload.get("plan", "free"))).lower()

        # Handle features field
        features_raw = payload.get("enabled_features")
        if features_raw is not None and isinstance(features_raw, list):
            features = list(features_raw)
        elif isinstance(payload.get("features"), dict):
            features = [k for k, v in payload["features"].items() if v]
        else:
            features = list(FREE_DEFAULT_FEATURES)

        # Handle limits field
        limits_raw = payload.get("usage_limits", payload.get("limits", {}))
        limits = dict(limits_raw) if isinstance(limits_raw, dict) else dict(FREE_DEFAULT_LIMITS)

        # Handle expiration field
        exp_str = payload.get("expires_at_utc", "")
        if not exp_str and payload.get("exp"):
            try:
                exp_dt = datetime.datetime.fromtimestamp(int(payload["exp"]), tz=datetime.timezone.utc)
                exp_str = exp_dt.isoformat()
            except Exception:
                pass

        last_ver_dt = now_utc
        if last_verified_str:
            try:
                last_ver_dt = datetime.datetime.fromisoformat(last_verified_str)
                if last_ver_dt.tzinfo is None:
                    last_ver_dt = last_ver_dt.replace(tzinfo=datetime.timezone.utc)
            except Exception:
                last_ver_dt = now_utc

        # Check offline grace duration and detect clock rollback
        hours_since_online = (now_utc - last_ver_dt).total_seconds() / 3600.0
        if hours_since_online < -1.0:
            # System clock tampering detected (rolled back) -> treat grace as expired
            logger.warning("System clock rollback detected (%.1f h). Revoking offline grace.", hours_since_online)
            is_offline_exp = True
            offline_hours_rem = 0.0
        else:
            is_offline_exp = hours_since_online > MAX_OFFLINE_GRACE_HOURS
            offline_hours_rem = max(0.0, MAX_OFFLINE_GRACE_HOURS - hours_since_online)

        # Check subscription expiration
        sub_expired = False
        if exp_str:
            try:
                exp_dt = datetime.datetime.fromisoformat(exp_str)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=datetime.timezone.utc)
                if now_utc > exp_dt:
                    sub_expired = True
            except Exception:
                pass

        is_pro = (plan in ("pro_monthly", "pro_annual", "pro")) and not sub_expired and not is_offline_exp

        status = "expired" if (sub_expired or is_offline_exp) else ("offline_grace" if hours_since_online > 0 else "active")

        self._current_state = EntitlementState(
            user_id=user_id,
            plan_code="free" if (sub_expired or is_offline_exp) else plan,
            status=status,
            is_pro=is_pro,
            enabled_features=features if is_pro else list(FREE_DEFAULT_FEATURES),
            usage_limits=limits if is_pro else dict(FREE_DEFAULT_LIMITS),
            period_start_utc=payload.get("period_start_utc", payload.get("issued_at_utc", "")),
            period_end_utc=exp_str,
            last_verified_at_utc=last_ver_dt.isoformat(),
            offline_hours_remaining=offline_hours_rem,
            is_offline_expired=is_offline_exp,
        )

    def is_offline_expired(self, now_dt: Optional[datetime.datetime] = None) -> bool:
        """Determines dynamically if offline grace (72 hours) has lapsed since last verified online timestamp."""
        with self.lock:
            if self._current_state.is_offline_expired:
                return True
            if self._current_state.offline_hours_remaining <= 0.0 and self._current_state.last_verified_at_utc:
                return True

            last_ver_str = self._current_state.last_verified_at_utc
            if not last_ver_str:
                return False

            now = now_dt or datetime.datetime.now(datetime.timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=datetime.timezone.utc)

            try:
                last_ver_dt = datetime.datetime.fromisoformat(last_ver_str)
                if last_ver_dt.tzinfo is None:
                    last_ver_dt = last_ver_dt.replace(tzinfo=datetime.timezone.utc)
                hours_offline = (now - last_ver_dt).total_seconds() / 3600.0
                if hours_offline < -1.0:
                    return True  # Clock tampering detected
                return hours_offline > MAX_OFFLINE_GRACE_HOURS
            except Exception:
                return False

    def is_subscription_expired(self, now_dt: Optional[datetime.datetime] = None) -> bool:
        """Checks dynamically if the subscription period has elapsed."""
        with self.lock:
            exp_str = self._current_state.period_end_utc
            if not exp_str:
                return False
            now = now_dt or datetime.datetime.now(datetime.timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=datetime.timezone.utc)
            try:
                exp_dt = datetime.datetime.fromisoformat(exp_str)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=datetime.timezone.utc)
                return now > exp_dt
            except Exception:
                return False

    def set_user_context(self, user_id: Optional[str], email: Optional[str] = None) -> None:
        """Sets the active user ID and email context and validates current entitlement against it."""
        with self.lock:
            self._active_user_id = str(user_id) if user_id else "default"
            self._active_email = str(email).strip().lower() if email else ""
            if is_master_developer(self._active_user_id, self._active_email):
                logger.info("Master Developer context verified for %s / %s — full Pro access enabled", self._active_email, self._active_user_id)
                return
            # If current state belongs to another specific user, clear cached token / fall back to free
            if self._active_user_id != "default" and self._current_state.user_id != "default":
                if self._current_state.user_id != self._active_user_id:
                    logger.info(
                        "Entitlement user mismatch (cached: %s vs active: %s); resetting state to Free",
                        self._current_state.user_id,
                        self._active_user_id,
                    )
                    self._current_state = EntitlementState(
                        user_id=self._active_user_id, plan_code="free", is_pro=False
                    )

    def is_pro(self, now_dt: Optional[datetime.datetime] = None) -> bool:
        """
        Dynamic Pro evaluation: True ONLY if:
        1. Base entitlement indicates Pro plan.
        2. Entitlement user_id matches active user context.
        3. Subscription is not expired.
        4. Offline grace period (72 hours) has not lapsed.
        Or unconditionally True for verified Master Developer.
        """
        with self.lock:
            if is_master_developer(getattr(self, "_active_user_id", ""), getattr(self, "_active_email", "")):
                return True
            state = self._current_state
            if not state.is_pro:
                return False
            if state.plan_code not in ("pro_monthly", "pro_annual", "pro"):
                return False
            if self._active_user_id != "default" and state.user_id != "default":
                if state.user_id != self._active_user_id:
                    return False
            if self.is_subscription_expired(now_dt):
                return False
            if self.is_offline_expired(now_dt):
                return False
            return True

    def is_feature_enabled(self, feature_code: str) -> bool:
        """Evaluates whether a feature is permitted. Pro features require active (non-expired) Pro status."""
        with self.lock:
            if self.is_pro():
                return True
            return feature_code in self._current_state.enabled_features

    def get_effective_entitlement(self) -> Dict[str, Any]:
        """Returns standard dictionary representing dynamic effective plan, limits, and offline grace."""
        with self.lock:
            if is_master_developer(getattr(self, "_active_user_id", ""), getattr(self, "_active_email", "")):
                return {
                    "user_id": self._active_user_id,
                    "plan": "pro_annual",
                    "plan_code": "pro_annual",
                    "plan_name": "VOX Pro",
                    "is_pro": True,
                    "status": "active",
                    "limits": {
                        "dictation_words_weekly": None,
                        "meeting_minutes_weekly": None,
                        "meeting_sessions_weekly": None,
                        "meeting_session_cap_min": None,
                        "ask_vox_queries_weekly": None,
                        "allow_meeting_overrun": True,
                    },
                    "features": list(PRO_DEFAULT_FEATURES),
                    "in_offline_grace": False,
                    "offline_hours_remaining": 999999.0,
                    "is_offline_expired": False,
                    "is_subscription_expired": False,
                    "last_verified_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "subscription_expires_at_utc": "2126-11-16T12:00:00Z",
                }
            state = self.get_state()
            pro_active = self.is_pro()
            offline_exp = self.is_offline_expired()
            sub_exp = self.is_subscription_expired()

            hours_rem = state.offline_hours_remaining
            if state.last_verified_at_utc:
                try:
                    now = datetime.datetime.now(datetime.timezone.utc)
                    last_ver_dt = datetime.datetime.fromisoformat(state.last_verified_at_utc)
                    if last_ver_dt.tzinfo is None:
                        last_ver_dt = last_ver_dt.replace(tzinfo=datetime.timezone.utc)
                    elapsed = (now - last_ver_dt).total_seconds() / 3600.0
                    if elapsed < -1.0:
                        hours_rem = 0.0
                    elif elapsed <= 0.001:
                        hours_rem = MAX_OFFLINE_GRACE_HOURS
                    else:
                        hours_rem = max(0.0, round(MAX_OFFLINE_GRACE_HOURS - elapsed, 2))
                except Exception:
                    pass

            return {
                "user_id": state.user_id,
                "plan": state.plan_code if pro_active else "free",
                "plan_name": "VOX Pro" if pro_active else "VOX Free",
                "is_pro": pro_active,
                "status": "expired" if (offline_exp or sub_exp) else state.status,
                "limits": state.usage_limits if pro_active else dict(FREE_DEFAULT_LIMITS),
                "features": state.enabled_features if pro_active else list(FREE_DEFAULT_FEATURES),
                "in_offline_grace": state.status == "offline_grace" or (not offline_exp and hours_rem < MAX_OFFLINE_GRACE_HOURS and hours_rem > 0.0),
                "offline_hours_remaining": hours_rem,
                "is_offline_expired": offline_exp,
            }

    def save_entitlement_token(self, token: str, expected_user_id: Optional[str] = None) -> Tuple[bool, str]:
        """Validate, apply, and securely persist an entitlement token received from the backend."""
        with self.lock:
            target_user_id = expected_user_id or (self._active_user_id if self._active_user_id != "default" else None)
            valid, payload, err = verify_and_decode_entitlement_token(token, self.verify_key, expected_user_id=target_user_id)
            if not valid or not payload:
                return False, f"Entitlement verification failed: {err}"

            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._apply_payload(payload, now_iso)

            # Persist via DPAPI
            vault_data = json.dumps({"token": token, "last_verified_at_utc": now_iso}).encode("utf-8")
            encrypted = dpapi_encrypt(vault_data, description="vox_entitlement_vault")
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_bytes(encrypted)

            logger.info(
                "Saved and applied entitlement token: plan=%s, is_pro=%s",
                self._current_state.plan_code,
                self._current_state.is_pro,
            )
            return True, ""

    def get_state(self) -> EntitlementState:
        with self.lock:
            return self._current_state

    def clear_entitlement(self) -> None:
        """Called on user logout. Reverts to default Free entitlement while preserving all local SQLite data."""
        with self.lock:
            if self.cache_file.exists():
                try:
                    self.cache_file.unlink()
                except Exception:
                    pass
            self._active_user_id = "default"
            self._current_state = EntitlementState(user_id="default", plan_code="free", is_pro=False)
            logger.info("Entitlement cleared, reverted to Free mode")

    def revert_to_free(self) -> None:
        """Alias for clear_entitlement."""
        self.clear_entitlement()

    def save_token_to_vault(self, token: str) -> Tuple[bool, str]:
        """Alias for save_entitlement_token."""
        return self.save_entitlement_token(token)

"""
VOX Entitlement Engine & Feature Restriction Manager.

Consumes signed entitlement tokens issued by the serverless backend,
verifies cryptographic signatures, stores cached credentials via Windows DPAPI,
enforces bounded 72-hour offline operation, and gates Pro functionality
without risking user data during subscription downgrades.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Tuple

from voxlocal.calendar.auth_service import dpapi_decrypt, dpapi_encrypt
from voxlocal.core.logger import get_app_root, get_logger

logger = get_logger("entitlement")

# Default verification public secret used for offline validation when no custom key is provided
DEFAULT_ENTITLEMENT_VERIFY_KEY = "vox_entitlement_sig_v1_public_verify_key_2026"
MAX_OFFLINE_GRACE_HOURS = 72.0


@dataclass
class EntitlementState:
    user_id: str = "default"
    plan_code: str = "free"               # "free", "pro_monthly", "pro_annual"
    status: str = "active"                # "active", "past_due", "expired", "offline_grace"
    is_pro: bool = False
    enabled_features: List[str] = field(default_factory=lambda: [
        "dictation", "dual_track_recording", "meeting_summaries", "local_vocabulary", "grey_text_preview"
    ])
    usage_limits: Dict[str, Any] = field(default_factory=lambda: {
        "dictation_words_weekly": 3000,
        "meeting_minutes_weekly": 180.0,
        "meeting_sessions_weekly": 5,
        "meeting_session_cap_min": 90.0,
        "ask_vox_queries_weekly": 10,
    })
    period_start_utc: str = ""
    period_end_utc: str = ""
    last_verified_at_utc: str = ""
    offline_hours_remaining: float = MAX_OFFLINE_GRACE_HOURS
    is_offline_expired: bool = False


def sign_entitlement_payload(payload: Dict[str, Any], secret_key: str) -> str:
    """Helper used by serverless backend / test suites to generate signed entitlement tokens."""
    raw_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_json).decode("ascii").rstrip("=")
    sig = hmac.new(secret_key.encode("utf-8"), b64_payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64_payload}.{sig}"


def verify_and_decode_entitlement_token(token: str, verify_key: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Verify cryptographic signature and decode entitlement claims."""
    if not token or "." not in token:
        return False, None, "Invalid token format"

    parts = token.strip().split(".")
    if len(parts) != 2:
        return False, None, "Invalid token structure"

    b64_payload, expected_sig = parts[0], parts[1]

    # Recompute HMAC signature
    computed_sig = hmac.new(verify_key.encode("utf-8"), b64_payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, computed_sig):
        return False, None, "Invalid entitlement token signature"

    # Decode payload
    rem = len(b64_payload) % 4
    padded_b64 = b64_payload + ("=" * (4 - rem) if rem else "")
    try:
        raw_json = base64.urlsafe_b64decode(padded_b64.encode("ascii")).decode("utf-8")
        payload = json.loads(raw_json)
        return True, payload, ""
    except Exception as exc:
        return False, None, f"Failed decoding payload: {exc}"


class EntitlementManager:
    """
    Manages local entitlement lifecycle, DPAPI-cached token storage,
    72-hour bounded offline grace, and feature query interfaces.
    """

    def __init__(self, cache_file: Optional[Path] = None, verify_key: str = DEFAULT_ENTITLEMENT_VERIFY_KEY) -> None:
        self.lock = threading.RLock()
        self.verify_key = os.environ.get("VOX_ENTITLEMENT_VERIFY_KEY", verify_key)
        if cache_file is None:
            root = get_app_root()
            self.cache_file = root / "data" / "entitlement.vault"
        else:
            self.cache_file = Path(cache_file)

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
        plan = str(payload.get("plan_code", "free")).lower()
        features = list(payload.get("enabled_features", []))
        limits = dict(payload.get("usage_limits", {}))
        exp_str = payload.get("expires_at_utc", "")

        last_ver_dt = now_utc
        if last_verified_str:
            try:
                last_ver_dt = datetime.datetime.fromisoformat(last_verified_str)
                if last_ver_dt.tzinfo is None:
                    last_ver_dt = last_ver_dt.replace(tzinfo=datetime.timezone.utc)
            except Exception:
                last_ver_dt = now_utc

        # Check offline grace duration
        hours_since_online = (now_utc - last_ver_dt).total_seconds() / 3600.0
        offline_hours_rem = max(0.0, MAX_OFFLINE_GRACE_HOURS - hours_since_online)
        is_offline_exp = hours_since_online > MAX_OFFLINE_GRACE_HOURS

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

        is_pro = (plan in ("pro_monthly", "pro_annual", "pro")) and not sub_expired

        self._current_state = EntitlementState(
            user_id=user_id,
            plan_code="free" if sub_expired else plan,
            status="expired" if sub_expired else ("offline_grace" if hours_since_online > 0 else "active"),
            is_pro=is_pro,
            enabled_features=features if not sub_expired else ["dictation", "dual_track_recording", "meeting_summaries"],
            usage_limits=limits,
            period_start_utc=payload.get("period_start_utc", ""),
            period_end_utc=payload.get("period_end_utc", ""),
            last_verified_at_utc=last_ver_dt.isoformat(),
            offline_hours_remaining=offline_hours_rem,
            is_offline_expired=is_offline_exp,
        )

    def save_entitlement_token(self, token: str) -> Tuple[bool, str]:
        """Validate, apply, and securely persist an entitlement token received from the backend."""
        with self.lock:
            valid, payload, err = verify_and_decode_entitlement_token(token, self.verify_key)
            if not valid or not payload:
                return False, f"Entitlement verification failed: {err}"

            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._apply_payload(payload, now_iso)

            # Persist via DPAPI
            vault_data = json.dumps({"token": token, "last_verified_at_utc": now_iso}).encode("utf-8")
            encrypted = dpapi_encrypt(vault_data, description="vox_entitlement_vault")
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_bytes(encrypted)

            logger.info("Saved and applied entitlement token: plan=%s, is_pro=%s", self._current_state.plan_code, self._current_state.is_pro)
            return True, ""

    def get_state(self) -> EntitlementState:
        with self.lock:
            return self._current_state

    def is_feature_enabled(self, feature_code: str) -> bool:
        with self.lock:
            if self._current_state.is_pro:
                return True
            return feature_code in self._current_state.enabled_features

    def clear_entitlement(self) -> None:
        """Called on user logout. Reverts to default Free entitlement while preserving all local SQLite data."""
        with self.lock:
            if self.cache_file.exists():
                try:
                    self.cache_file.unlink()
                except Exception:
                    pass
            self._current_state = EntitlementState(user_id="default", plan_code="free", is_pro=False)
            logger.info("Entitlement cleared, reverted to Free mode")

    def revert_to_free(self) -> None:
        """Alias for clear_entitlement."""
        self.clear_entitlement()

    def save_token_to_vault(self, token: str) -> Tuple[bool, str]:
        """Alias for save_entitlement_token."""
        return self.save_entitlement_token(token)

    def get_effective_entitlement(self) -> Dict[str, Any]:
        """Returns standard dictionary representing effective plan, limits, and offline grace."""
        with self.lock:
            state = self.get_state()
            return {
                "user_id": state.user_id,
                "plan": state.plan_code,
                "plan_name": "VOX Pro" if state.is_pro else "VOX Free",
                "is_pro": state.is_pro,
                "status": state.status,
                "limits": state.usage_limits,
                "features": state.enabled_features,
                "in_offline_grace": state.status == "offline_grace",
                "offline_hours_remaining": state.offline_hours_remaining,
            }

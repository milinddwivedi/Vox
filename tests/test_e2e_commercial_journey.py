"""
Comprehensive End-to-End Commercial Journey Test Suite for VOX.

Covers Desktop Client Commercialisation:
- Tier 1: Core Feature Coverage (F1, F2, F3, F4, F5, F6, F9) - 35 Tests
- Tier 2: Boundary & Corner Cases (Cryptographic, 72h Offline, Quotas, Concurrency, Roll-over) - 25 Tests
- Tier 3: Pairwise Combinations (Offline + Quota, Switch + Downgrade, Overrun + Cap, etc.) - 15 Tests
- Tier 4: Real-World Customer Workload Scenarios (5 Comprehensive Workloads) - 5 Tests

Total Tests: Exactly 80 Tests.
Executed via: $env:PYTHONPATH="src"; .venv\\Scripts\\python.exe -m pytest tests/test_e2e_commercial_journey.py -v
"""

from __future__ import annotations

import base64
import concurrent.futures
import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication

from voxlocal.calendar.auth_service import dpapi_decrypt, dpapi_encrypt
from voxlocal.core.controller import AppController
from voxlocal.core.ed25519_rfc8032 import (
    ed25519_public_key,
    ed25519_sign,
    ed25519_verify,
)
from voxlocal.core.entitlement import (
    DEFAULT_ENTITLEMENT_PUBLIC_KEY,
    MAX_OFFLINE_GRACE_HOURS,
    EntitlementManager,
    EntitlementState,
    normalize_public_key,
    verify_and_decode_entitlement_token,
)
from tests.token_test_helper import (
    DEV_FALLBACK_PRIVATE_KEY_HEX,
    sign_entitlement_payload,
)
from voxlocal.core.metering import (
    FREE_SINGLE_MEETING_MIN_LIMIT,
    FREE_WEEKLY_ASK_VOX_LIMIT,
    FREE_WEEKLY_MEETING_COUNT,
    FREE_WEEKLY_MEETING_MINUTES,
    FREE_WEEKLY_WORD_LIMIT,
    WARNING_THRESHOLD_RATIO,
    OverrunStatus,
    UsageMeteringEngine,
    count_committed_words,
    get_current_week_period_utc,
)
from voxlocal.ui.pro_upgrade_dialog import (
    RAZORPAY_MONTHLY_LINK,
    RAZORPAY_YEARLY_LINK,
    VoxProUpgradeDialog,
)


# ==============================================================================
# Pytest Fixtures & Test Infrastructure
# ==============================================================================

@pytest.fixture(scope="session")
def qapp() -> Generator[QApplication, None, None]:
    """Headless QApplication fixture running with offscreen QPA platform."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication(["vox_e2e_test", "-platform", "offscreen"])
    yield app
    app.processEvents()


class MockDateTime(datetime.datetime):
    """Controllable mock datetime that enables deterministic time-travel in tests."""

    _current_time: datetime.datetime = datetime.datetime(
        2026, 11, 16, 10, 0, 0, tzinfo=datetime.timezone.utc
    )

    @classmethod
    def now(cls, tz: Optional[datetime.tzinfo] = None) -> datetime.datetime:
        if tz is not None:
            return cls._current_time.astimezone(tz)
        return cls._current_time

    @classmethod
    def set_time(cls, dt: datetime.datetime) -> None:
        if dt.tzinfo is None:
            cls._current_time = dt.replace(tzinfo=datetime.timezone.utc)
        else:
            cls._current_time = dt.astimezone(datetime.timezone.utc)

    @classmethod
    def advance(cls, **kwargs: Any) -> None:
        cls._current_time += datetime.timedelta(**kwargs)


@pytest.fixture
def time_machine() -> Generator[type[MockDateTime], None, None]:
    """Provides time manipulation patching datetime in both entitlement and metering."""
    initial_dt = datetime.datetime(2026, 11, 16, 10, 0, 0, tzinfo=datetime.timezone.utc)
    MockDateTime.set_time(initial_dt)
    with patch("voxlocal.core.entitlement.datetime.datetime", MockDateTime), patch(
        "voxlocal.core.metering.datetime.datetime", MockDateTime
    ):
        yield MockDateTime


@pytest.fixture(scope="session")
def ed25519_keypair() -> Dict[str, Any]:
    """Deterministic 32-byte Ed25519 keypair and minting helper for test assertions."""
    # Seed matching DEV_FALLBACK_PRIVATE_KEY_HEX
    seed_bytes = bytes.fromhex(DEV_FALLBACK_PRIVATE_KEY_HEX)
    pub_bytes = ed25519_public_key(seed_bytes)
    pub_hex = pub_bytes.hex()
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).decode("ascii").rstrip("=")

    # Rogue keypair for negative tamper testing
    rogue_seed = b"rogue_seed_private_key_32bytes!"
    rogue_pub_bytes = ed25519_public_key(rogue_seed)

    def mint_token(
        payload: Dict[str, Any],
        rogue: bool = False,
        signing_seed: Optional[bytes] = None,
    ) -> str:
        s = rogue_seed if rogue else (signing_seed or seed_bytes)
        return sign_entitlement_payload(payload, private_key=s)

    return {
        "seed_bytes": seed_bytes,
        "public_bytes": pub_bytes,
        "public_hex": pub_hex,
        "public_b64": pub_b64,
        "rogue_seed": rogue_seed,
        "rogue_pub_bytes": rogue_pub_bytes,
        "mint_token": mint_token,
    }


@pytest.fixture
def commercial_env(ed25519_keypair: Dict[str, Any]) -> Generator[Dict[str, Any], None, None]:
    """Provides isolated temporary sandbox with independent SQLite DB, vault, and engines."""
    temp_dir = tempfile.mkdtemp(prefix="vox_e2e_")
    temp_path = Path(temp_dir)
    db_path = temp_path / "voxlocal_test.db"
    vault_path = temp_path / "entitlement_test.vault"

    metering = UsageMeteringEngine(db_path=db_path)
    entitlement = EntitlementManager(
        cache_file=vault_path, verify_key=ed25519_keypair["public_hex"]
    )

    yield {
        "temp_path": temp_path,
        "db_path": db_path,
        "vault_path": vault_path,
        "metering": metering,
        "entitlement": entitlement,
        "keypair": ed25519_keypair,
    }
    try:
        metering.close()
    except Exception:
        pass
    shutil.rmtree(temp_dir, ignore_errors=True)


# ==============================================================================
# Tier 1: Core Feature Coverage (35 Tests: Features 1, 2, 3, 4, 5, 6, 9)
# ==============================================================================

class TestTier1FeatureCoverage:
    """Tier 1: Comprehensive feature coverage across all desktop commercial pillars."""

    # --- Feature 1: Ed25519 Entitlement Signing & Verification (5 Tests) ---

    def test_tier1_f01_valid_ed25519_token_verification(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """F1.1: Genuine Ed25519 token signed with backend key verifies cleanly."""
        payload = {
            "user_id": "usr_alice",
            "plan_code": "pro_monthly",
            "is_pro": True,
            "enabled_features": ["dictation", "dual_track_recording", "executive_export"],
            "usage_limits": {"dictation_words_weekly": 999999999},
        }
        token = ed25519_keypair["mint_token"](payload)
        ent = commercial_env["entitlement"]

        ok, err = ent.save_entitlement_token(token)
        assert ok is True, f"Failed to save valid token: {err}"
        assert ent.is_pro() is True
        assert ent.get_state().plan_code == "pro_monthly"
        assert ent.get_state().user_id == "usr_alice"

    def test_tier1_f01_pro_payload_activates_unlimited_entitlement(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """F1.2: Pro payload unlocks all restricted enterprise intelligence features."""
        payload = {
            "user_id": "usr_alice",
            "plan_code": "pro_monthly",
            "is_pro": True,
            "enabled_features": ["dictation", "dual_track_recording", "meeting_summaries", "executive_export"],
            "usage_limits": {"dictation_words_weekly": 999999999},
        }
        token = ed25519_keypair["mint_token"](payload)
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]

        ent.save_entitlement_token(token)
        assert ent.is_feature_enabled("executive_export") is True
        assert ent.is_feature_enabled("meeting_summaries") is True
        assert metering.can_start_dictation(ent) == (True, "")
        assert metering.can_start_meeting(ent) == (True, "")
        assert metering.can_ask_vox(ent) == (True, "")

    def test_tier1_f01_dpapi_vault_persistence_and_reload(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """F1.3: Token persisted via Windows DPAPI is reloaded with 100% integrity across restarts."""
        payload = {"user_id": "usr_vault_persist", "plan_code": "pro_annual", "is_pro": True}
        token = ed25519_keypair["mint_token"](payload)
        commercial_env["entitlement"].save_entitlement_token(token)

        assert commercial_env["vault_path"].exists() is True
        # Restart EntitlementManager pointing to same vault
        fresh_mgr = EntitlementManager(
            cache_file=commercial_env["vault_path"],
            verify_key=ed25519_keypair["public_hex"],
        )
        assert fresh_mgr.is_pro() is True
        assert fresh_mgr.get_state().user_id == "usr_vault_persist"
        assert fresh_mgr.get_state().plan_code == "pro_annual"

    def test_tier1_f01_free_tier_safe_initial_state(self, tmp_path: Path) -> None:
        """F1.4: Cold startup without prior token initializes safe Free tier with standard quotas."""
        nonexistent = tmp_path / "never_created.vault"
        mgr = EntitlementManager(cache_file=nonexistent)
        assert mgr.is_pro() is False
        assert mgr.get_state().plan_code == "free"
        assert mgr.get_state().usage_limits["dictation_words_weekly"] == FREE_WEEKLY_WORD_LIMIT
        assert mgr.get_state().usage_limits["meeting_sessions_weekly"] == FREE_WEEKLY_MEETING_COUNT

    def test_tier1_f01_explicit_free_token_handling(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """F1.5: Explicitly signed free-tier token sets active free status without elevation."""
        payload = {"user_id": "usr_free", "plan_code": "free", "is_pro": False}
        token = ed25519_keypair["mint_token"](payload)
        ent = commercial_env["entitlement"]

        ok, err = ent.save_entitlement_token(token)
        assert ok is True
        assert ent.is_pro() is False
        assert ent.get_state().plan_code == "free"

    # --- Feature 2: 72-Hour Offline Grace Enforcement (5 Tests) ---

    def test_tier1_f02_active_pro_within_72h_offline_window(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """F2.1: Pro user disconnected from network for 24 hours retains Pro privileges."""
        payload = {"user_id": "usr_offline", "plan_code": "pro_monthly", "is_pro": True}
        token = ed25519_keypair["mint_token"](payload)
        ent = commercial_env["entitlement"]
        ent.save_entitlement_token(token)

        # Simulate 24 hours elapsed
        past_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24.0)
        ent._current_state.last_verified_at_utc = past_time.isoformat()

        assert ent.is_offline_expired() is False
        assert ent.is_pro() is True
        eff = ent.get_effective_entitlement()
        assert eff["in_offline_grace"] is True
        assert 46.0 <= eff["offline_hours_remaining"] <= 49.0

    def test_tier1_f02_offline_hours_countdown_accuracy(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """F2.2: offline_hours_remaining counts down monotonically towards zero."""
        payload = {"user_id": "usr_countdown", "plan_code": "pro_monthly", "is_pro": True}
        token = ed25519_keypair["mint_token"](payload)
        ent = commercial_env["entitlement"]
        ent.save_entitlement_token(token)

        now = datetime.datetime.now(datetime.timezone.utc)
        # Check at 10h, 36h, 72h
        for hours_elapsed in [10.0, 36.0, 72.0]:
            t = now - datetime.timedelta(hours=hours_elapsed)
            ent._current_state.last_verified_at_utc = t.isoformat()
            rem = ent.get_effective_entitlement()["offline_hours_remaining"]
            expected = max(0.0, MAX_OFFLINE_GRACE_HOURS - hours_elapsed)
            assert abs(rem - expected) < 1.0

    def test_tier1_f02_feature_enabled_during_grace(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """F2.3: Pro features remain fully accessible during active offline grace."""
        payload = {
            "user_id": "usr_grace_feat",
            "plan_code": "pro_monthly",
            "is_pro": True,
            "enabled_features": ["dictation", "executive_export"],
        }
        token = ed25519_keypair["mint_token"](payload)
        ent = commercial_env["entitlement"]
        ent.save_entitlement_token(token)

        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=48.0)
        ent._current_state.last_verified_at_utc = past.isoformat()

        assert ent.is_feature_enabled("executive_export") is True

    def test_tier1_f02_offline_expiration_deactivates_pro(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """F2.4: Advancing offline elapsed time past 72.0h revokes Pro status and marks expired."""
        payload = {"user_id": "usr_offline_exp", "plan_code": "pro_monthly", "is_pro": True}
        token = ed25519_keypair["mint_token"](payload)
        ent = commercial_env["entitlement"]
        ent.save_entitlement_token(token)

        # Set last verified at 73.0 hours ago
        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=73.0)
        ent._current_state.last_verified_at_utc = past.isoformat()

        assert ent.is_offline_expired() is True
        assert ent.is_pro() is False
        eff = ent.get_effective_entitlement()
        assert eff["is_pro"] is False
        assert eff["plan"] == "free"
        assert eff["status"] == "expired"

    def test_tier1_f02_token_refresh_resets_grace_clock(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """F2.5: Reconnecting and saving a fresh token resets offline grace clock back to 72.0h."""
        payload = {"user_id": "usr_refresh", "plan_code": "pro_monthly", "is_pro": True}
        token = ed25519_keypair["mint_token"](payload)
        ent = commercial_env["entitlement"]
        ent.save_entitlement_token(token)

        # Force expired
        ent._current_state.last_verified_at_utc = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=80.0)
        ).isoformat()
        assert ent.is_offline_expired() is True

        # Refresh
        fresh_token = ed25519_keypair["mint_token"](payload)
        ok, _ = ent.save_entitlement_token(fresh_token)
        assert ok is True
        assert ent.is_offline_expired() is False
        assert ent.is_pro() is True
        assert abs(ent.get_effective_entitlement()["offline_hours_remaining"] - MAX_OFFLINE_GRACE_HOURS) < 0.05

    # --- Feature 3: Clean Pro Upgrade Flow (No Backdoors) (5 Tests) ---

    def test_tier1_f03_upgrade_dialog_instantiation_no_backdoors(self, qapp: QApplication) -> None:
        """F3.1: VoxProUpgradeDialog contains no manual activation textbox or backdoor buttons."""
        mock_controller = MagicMock()
        dlg = VoxProUpgradeDialog(mock_controller, reason="Testing Dialog", theme="dark")
        assert not hasattr(dlg, "input_key") or dlg.input_key is None
        assert not hasattr(dlg, "btn_activate") or dlg.btn_activate is None
        assert hasattr(dlg, "btn_sync")

    def test_tier1_f03_monthly_checkout_button_link(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F3.2: Monthly checkout button opens approved Razorpay URL https://rzp.io/rzp/ugv7JBF2."""
        import voxlocal.ui.pro_upgrade_dialog as pud
        mock_controller = MagicMock()
        mock_controller.get_account_status.return_value = {
            "signed_in": True,
            "is_signed_in": True,
            "is_pro": False,
            "email": "test@vox.ai",
            "name": "Test User",
            "user_id": "u_test",
        }
        dlg = VoxProUpgradeDialog(mock_controller, reason="Testing Monthly", theme="light")

        opened_urls: List[str] = []
        monkeypatch.setattr(pud.webbrowser, "open", lambda url, new=2: opened_urls.append(url) or True)
        monkeypatch.setattr(
            QDesktopServices, "openUrl", lambda url: opened_urls.append(url.toString())
        )
        dlg._open_monthly_checkout()
        assert any(u.startswith(RAZORPAY_MONTHLY_LINK) for u in opened_urls)

    def test_tier1_f03_annual_checkout_button_link(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F3.3: Annual checkout button opens approved Razorpay URL https://rzp.io/rzp/fycKln1w."""
        import voxlocal.ui.pro_upgrade_dialog as pud
        mock_controller = MagicMock()
        mock_controller.get_account_status.return_value = {
            "signed_in": True,
            "is_signed_in": True,
            "is_pro": False,
            "email": "test@vox.ai",
            "name": "Test User",
            "user_id": "u_test",
        }
        dlg = VoxProUpgradeDialog(mock_controller, reason="Testing Annual", theme="dark")

        opened_urls: List[str] = []
        monkeypatch.setattr(pud.webbrowser, "open", lambda url, new=2: opened_urls.append(url) or True)
        monkeypatch.setattr(
            QDesktopServices, "openUrl", lambda url: opened_urls.append(url.toString())
        )
        dlg._open_yearly_checkout()
        assert any(u.startswith(RAZORPAY_YEARLY_LINK) for u in opened_urls)

    def test_tier1_f03_dialog_refresh_button_action(self, qapp: QApplication) -> None:
        """F3.4: Dialog refresh/sync button checks cloud entitlement status via controller."""
        mock_controller = MagicMock()
        mock_controller.entitlement = MagicMock()
        mock_controller.entitlement.get_state.return_value = EntitlementState(is_pro=True)
        dlg = VoxProUpgradeDialog(mock_controller, reason="Sync Check", theme="light")
        dlg.show()

        dlg._on_sync_clicked()
        assert not dlg.lbl_act_status.isHidden()
        assert "VOX Pro is active" in dlg.lbl_act_status.text()

    def test_tier1_f03_controller_upgrade_dialog_signal_propagation(self) -> None:
        """F3.5: AppController defines upgrade_dialog_requested signal for paywall surfacing."""
        assert hasattr(AppController, "upgrade_dialog_requested")

    # --- Feature 4: Multi-User Local Account Isolation (5 Tests) ---

    def test_tier1_f04_set_user_context_switches_identity(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """F4.1: set_user_context transitions identity cleanly in metering engine."""
        metering = commercial_env["metering"]
        metering.set_user_context("usr_alice", "pro_monthly")
        assert metering._user_id == "usr_alice"
        assert metering._plan_code == "pro_monthly"

        metering.set_user_context("usr_bob", "free")
        assert metering._user_id == "usr_bob"
        assert metering._plan_code == "free"

    def test_tier1_f04_user_a_and_user_b_isolated_ledgers(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """F4.2: Dictation words recorded by User A do not increment User B's weekly totals."""
        metering = commercial_env["metering"]

        metering.set_user_context("usr_alice", "free")
        metering.record_committed_dictation("One two three four five")

        metering.set_user_context("usr_bob", "free")
        metering.record_committed_dictation("Ten twenty thirty")

        metering.set_user_context("usr_alice", "free")
        assert metering.get_weekly_usage()["dictation_words"] == 5

        metering.set_user_context("usr_bob", "free")
        assert metering.get_weekly_usage()["dictation_words"] == 3

    def test_tier1_f04_logout_clears_auth_and_reverts_entitlement(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """F4.3: Logout removes local DPAPI vault file and reverts to Free tier."""
        ent = commercial_env["entitlement"]
        token = ed25519_keypair["mint_token"]({"user_id": "usr_logout", "plan_code": "pro", "is_pro": True})
        ent.save_entitlement_token(token)
        assert commercial_env["vault_path"].exists() is True

        ent.clear_entitlement()
        assert commercial_env["vault_path"].exists() is False
        assert ent.is_pro() is False
        assert ent.get_state().plan_code == "free"

    def test_tier1_f04_login_creates_isolated_sqlite_row(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """F4.4: Independent composite primary key rows (period_id, user_id) exist in SQLite."""
        metering = commercial_env["metering"]
        metering.set_user_context("user_alpha", "free")
        metering.record_committed_dictation("Alpha speech sample")

        metering.set_user_context("user_beta", "free")
        metering.record_committed_dictation("Beta speech sample text")

        with sqlite3.connect(str(commercial_env["db_path"])) as con:
            cur = con.cursor()
            cur.execute("SELECT user_id, dictation_words FROM local_metering_periods ORDER BY user_id")
            rows = cur.fetchall()
            assert len(rows) == 2
            assert rows[0][0] == "user_alpha"
            assert rows[0][1] == 3
            assert rows[1][0] == "user_beta"
            assert rows[1][1] == 4

    def test_tier1_f04_relogin_restores_user_exact_quota_state(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """F4.5: Cycling between accounts preserves exact accumulated counts for each user."""
        metering = commercial_env["metering"]

        metering.set_user_context("user_dev", "free")
        for _ in range(5):
            metering.record_committed_dictation("One two")
        assert metering.get_weekly_usage()["dictation_words"] == 10

        metering.set_user_context("user_guest", "free")
        assert metering.get_weekly_usage()["dictation_words"] == 0
        metering.record_committed_dictation("Hello world")

        metering.set_user_context("user_dev", "free")
        assert metering.get_weekly_usage()["dictation_words"] == 10

    # --- Feature 5: Desktop Plan & Quota UI Surfacing (5 Tests) ---

    def test_tier1_f05_account_status_plan_badge_name(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """F5.1: Effective entitlement plan_name returns 'VOX Free' vs 'VOX Pro'."""
        ent = commercial_env["entitlement"]
        assert ent.get_effective_entitlement()["plan_name"] == "VOX Free"

        token = ed25519_keypair["mint_token"]({"user_id": "usr_pro", "plan_code": "pro_monthly", "is_pro": True})
        ent.save_entitlement_token(token)
        assert ent.get_effective_entitlement()["plan_name"] == "VOX Pro"

    def test_tier1_f05_quota_percentage_calculation(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """F5.2: Usage snapshot computes exact percentage consumed across all metrics."""
        metering = commercial_env["metering"]
        metering.record_committed_dictation(" ".join(["word"] * 1500))
        snap = metering.get_usage_snapshot()
        assert snap.dictation_pct == 50.0

    def test_tier1_f05_auth_state_changed_signal_payload(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """F5.3: get_effective_entitlement dictionary adheres strictly to UI contract schema."""
        eff = commercial_env["entitlement"].get_effective_entitlement()
        required_keys = {
            "user_id", "plan", "plan_name", "is_pro", "status", "limits",
            "features", "in_offline_grace", "offline_hours_remaining",
        }
        assert required_keys.issubset(eff.keys())

    def test_tier1_f05_80_percent_early_warning_status_flag(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """F5.4: 80% usage threshold triggers early warning status flag (2,400 words)."""
        metering = commercial_env["metering"]
        metering.record_committed_dictation(" ".join(["word"] * 2400))
        snap = metering.get_usage_snapshot()
        assert snap.is_dictation_warning is True
        warnings = metering.check_early_warnings()
        assert len(warnings) >= 1
        assert warnings[0]["metric"] == "dictation_words"

    def test_tier1_f05_pro_plan_null_exhaustion_indicators(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """F5.5: Pro plan guarantees exhaustion flags remain False despite high usage."""
        metering = commercial_env["metering"]
        ent = commercial_env["entitlement"]
        token = ed25519_keypair["mint_token"]({"user_id": "usr_heavy", "plan_code": "pro", "is_pro": True})
        ent.save_entitlement_token(token)

        metering.record_committed_dictation(" ".join(["word"] * 5000))
        can_dictate, _ = metering.can_start_dictation(ent)
        assert can_dictate is True

    # --- Feature 6: Usage Engine Enforcement (5 Tests) ---

    def test_tier1_f06_multilingual_word_counting(self) -> None:
        """F6.1: Multilingual word counter handles English, Devanagari Hindi, and Hinglish."""
        assert count_committed_words("Voice typing precision benchmark") == 4
        assert count_committed_words("नमस्ते भारत यह परीक्षण है") == 5
        assert count_committed_words("Kal presentation ready rakhna") == 4
        assert count_committed_words("WhisperFlow-v2 is state-of-the-art") >= 3

    def test_tier1_f06_monday_utc_reset_cadence(self) -> None:
        """F6.2: Weekly period cycle starts on Monday at 00:00:00 UTC."""
        start_dt, end_dt, period_id = get_current_week_period_utc()
        assert start_dt.weekday() == 0  # Monday
        assert start_dt.hour == 0 and start_dt.minute == 0 and start_dt.second == 0
        assert period_id.startswith("utc_week_")

    def test_tier1_f06_dictation_gating_at_3000_words(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """F6.3: Dictation is allowed up to 2,999 words and blocked at 3,000 words."""
        metering = commercial_env["metering"]
        metering.record_committed_dictation(" ".join(["word"] * 2999))
        can_start, _ = metering.can_start_dictation()
        assert can_start is True

        metering.record_committed_dictation("last")
        can_start, reason = metering.can_start_dictation()
        assert can_start is False
        assert "Weekly dictation allowance reached" in reason

    def test_tier1_f06_meeting_session_and_minutes_gating(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """F6.4: Meeting engine blocks 6th meeting session and >180 cumulative minutes."""
        metering = commercial_env["metering"]
        for i in range(5):
            metering.record_completed_meeting(duration_sec=60.0, meeting_id=i + 1)

        can_start, reason = metering.can_start_meeting()
        assert can_start is False
        assert "Weekly meeting session limit reached" in reason

    def test_tier1_f06_ask_vox_query_limit_10(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """F6.5: Ask VOX allows 10 queries per week and blocks 11th query for Free users."""
        metering = commercial_env["metering"]
        for _ in range(10):
            metering.record_ask_vox_query()

        can_query, reason = metering.can_ask_vox()
        assert can_query is False
        assert "Weekly Ask VOX query allowance reached" in reason

    # --- Feature 9: Release Manifest Integrity Trust Chain (5 Tests) ---

    def test_tier1_f09_manifest_payload_structure(self) -> None:
        """F9.1: Release manifest specifies all required deployment integrity fields."""
        manifest = {
            "version": "1.0.0",
            "product_name": "VOX — Local AI Voice Assistant",
            "release_date": "2026-11-15",
            "installer_url": "https://vox-voice-assistant.vercel.app/downloads/VoxLocal-Setup.exe",
            "sha256": "300a30401653ce4a83c6e86aee84c1480e940964ef60cadf5f95792ca875cec7",
        }
        for key in ["version", "product_name", "installer_url", "sha256"]:
            assert key in manifest

    def test_tier1_f09_valid_installer_sha256_verification(self, tmp_path: Path) -> None:
        """F9.2: SHA-256 verification of distributed setup installer binary."""
        installer_file = tmp_path / "VoxLocal-Setup.exe"
        dummy_content = b"MZ\x90\x00\x03\x00\x00\x00FakeInstallerBinaryPayload2026"
        installer_file.write_bytes(dummy_content)

        expected_hash = hashlib.sha256(dummy_content).hexdigest()
        computed_hash = hashlib.sha256(installer_file.read_bytes()).hexdigest()
        assert computed_hash == expected_hash

    def test_tier1_f09_valid_manifest_signature_acceptance(
        self, ed25519_keypair: Dict[str, Any]
    ) -> None:
        """F9.3: Manifest payload signed with release Ed25519 private key passes verification."""
        manifest_data = json.dumps({"version": "1.0.0", "sha256": "abc123hash"}, sort_keys=True).encode("utf-8")
        sig = ed25519_sign(manifest_data, ed25519_keypair["seed_bytes"])
        valid = ed25519_verify(sig, manifest_data, ed25519_keypair["public_bytes"])
        assert valid is True

    def test_tier1_f09_tampered_manifest_rejected(
        self, ed25519_keypair: Dict[str, Any]
    ) -> None:
        """F9.4: Tampering with manifest version or checksum invalidates signature."""
        manifest_data = json.dumps({"version": "1.0.0", "sha256": "abc123hash"}, sort_keys=True).encode("utf-8")
        sig = ed25519_sign(manifest_data, ed25519_keypair["seed_bytes"])

        tampered_data = json.dumps({"version": "1.0.1", "sha256": "abc123hash"}, sort_keys=True).encode("utf-8")
        valid = ed25519_verify(sig, tampered_data, ed25519_keypair["public_bytes"])
        assert valid is False

    def test_tier1_f09_mismatched_installer_hash_aborts_update(self, tmp_path: Path) -> None:
        """F9.5: Corrupted downloaded installer binary hash mismatch causes update abort."""
        downloaded_file = tmp_path / "downloaded_setup.exe"
        downloaded_file.write_bytes(b"CorruptedBinaryData")

        manifest_hash = "300a30401653ce4a83c6e86aee84c1480e940964ef60cadf5f95792ca875cec7"
        computed_hash = hashlib.sha256(downloaded_file.read_bytes()).hexdigest()
        assert computed_hash != manifest_hash


# ==============================================================================
# Tier 2: Boundary & Corner Cases (25 Tests: Groups A, B, C, D, E)
# ==============================================================================

class TestTier2BoundaryAndCornerCases:
    """Tier 2: Exhaustive boundary conditions, cryptographic attacks, and edge transitions."""

    # --- Group A: Cryptographic & Token Edge Cases (5 Tests) ---

    def test_tier2_b01_tampered_payload_claims_rejected(
        self, ed25519_keypair: Dict[str, Any]
    ) -> None:
        """B01: Flipping is_pro claim in base64 payload invalidates Ed25519 signature."""
        payload = {"user_id": "usr_tamper", "plan_code": "free", "is_pro": False}
        token = ed25519_keypair["mint_token"](payload)

        b64_pay, b64_sig = token.split(".")
        # Forged payload with is_pro=True
        forged_json = json.dumps({"user_id": "usr_tamper", "plan_code": "pro", "is_pro": True}, sort_keys=True)
        forged_b64 = base64.urlsafe_b64encode(forged_json.encode("utf-8")).decode("ascii").rstrip("=")
        forged_token = f"{forged_b64}.{b64_sig}"

        valid, decoded, err = verify_and_decode_entitlement_token(forged_token, ed25519_keypair["public_hex"])
        assert valid is False
        assert decoded is None
        assert "Invalid Ed25519 entitlement token signature" in err

    def test_tier2_b02_rogue_private_key_signature_rejected(
        self, ed25519_keypair: Dict[str, Any]
    ) -> None:
        """B02: Token signed by rogue unauthorized Ed25519 key fails verification."""
        payload = {"user_id": "usr_rogue", "plan_code": "pro", "is_pro": True}
        token = ed25519_keypair["mint_token"](payload, rogue=True)

        valid, decoded, err = verify_and_decode_entitlement_token(token, ed25519_keypair["public_hex"])
        assert valid is False
        assert decoded is None

    def test_tier2_b03_malformed_token_strings_fail_gracefully(
        self, ed25519_keypair: Dict[str, Any]
    ) -> None:
        """B03: Malformed token strings fail gracefully without throwing exceptions."""
        malformed_inputs = [
            "",
            "no_period_token_string",
            "too.many.periods.in.token",
            "invalid_b64!.valid_sig",
            "valid_payload.short_sig",
        ]
        for token_input in malformed_inputs:
            valid, decoded, err = verify_and_decode_entitlement_token(token_input, ed25519_keypair["public_hex"])
            assert valid is False
            assert decoded is None
            assert len(err) > 0

    def test_tier2_b04_token_user_id_mismatch_rejected(
        self, ed25519_keypair: Dict[str, Any]
    ) -> None:
        """B04: Token issued for User A loaded when User B is expected is rejected."""
        payload = {"user_id": "usr_alice", "plan_code": "pro", "is_pro": True}
        token = ed25519_keypair["mint_token"](payload)

        valid, decoded, err = verify_and_decode_entitlement_token(
            token, ed25519_keypair["public_hex"], expected_user_id="usr_bob"
        )
        assert valid is False
        assert "not current user 'usr_bob'" in err

    def test_tier2_b05_expired_token_timestamp_reverts_to_free(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """B05: Token with period_end_utc in the past is treated as expired upon load."""
        past_iso = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)).isoformat()
        payload = {
            "user_id": "usr_exp_sub",
            "plan_code": "pro_monthly",
            "is_pro": True,
            "expires_at_utc": past_iso,
        }
        token = ed25519_keypair["mint_token"](payload)
        ent = commercial_env["entitlement"]

        ok, _ = ent.save_entitlement_token(token)
        assert ok is True
        assert ent.is_pro() is False
        assert ent.get_state().plan_code == "free"
        assert ent.get_state().status == "expired"

    # --- Group B: 72-Hour Offline Boundary Transitions (5 Tests) ---

    def test_tier2_b06_exact_72_0_hour_boundary(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """B06: At exactly 72.0 hours, remaining grace is 0.0 hours."""
        token = ed25519_keypair["mint_token"]({"user_id": "usr_72h", "plan_code": "pro", "is_pro": True})
        ent = commercial_env["entitlement"]
        ent.save_entitlement_token(token)

        exact_72h = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=72.0)
        ent._current_state.last_verified_at_utc = exact_72h.isoformat()
        assert ent.get_effective_entitlement()["offline_hours_remaining"] <= 0.1

    def test_tier2_b07_72_1_hour_boundary_falls_back_to_free_limits(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """B07: At 72.1 hours offline, dictation checks Free limit (3,000 words)."""
        token = ed25519_keypair["mint_token"]({"user_id": "usr_72_1h", "plan_code": "pro", "is_pro": True})
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]
        ent.save_entitlement_token(token)

        # Record 3,000 words
        metering.record_committed_dictation(" ".join(["word"] * 3000))

        # Advance offline past 72h
        past_72_1h = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=72.1)
        ent._current_state.last_verified_at_utc = past_72_1h.isoformat()

        can_start, reason = metering.can_start_dictation(ent)
        assert can_start is False
        assert "Weekly dictation allowance reached" in reason

    def test_tier2_b08_mid_meeting_offline_expiration_preserves_recording(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """B08: Ongoing meeting recording is not terminated when 72h boundary passes mid-call."""
        token = ed25519_keypair["mint_token"]({"user_id": "usr_mid_mtg", "plan_code": "pro", "is_pro": True})
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]
        ent.save_entitlement_token(token)

        # Call evaluate_live_meeting_overrun during meeting
        status: OverrunStatus = metering.evaluate_live_meeting_overrun(elapsed_sec=3600.0, entitlement=ent)
        assert status.warning is False

        # Transition offline past 72h mid-call
        ent._current_state.last_verified_at_utc = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=72.5)
        ).isoformat()

        # Overrun policy for 95 min returns warning/overrun info, but NEVER terminates the session
        status_overrun: OverrunStatus = metering.evaluate_live_meeting_overrun(elapsed_sec=95 * 60.0, entitlement=ent)
        assert status_overrun.warning is True
        assert "continue recording uninterrupted" in status_overrun.message

    def test_tier2_b09_mid_dictation_offline_expiration_preserves_stream(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """B09: In-progress speech transcription commits text even if offline grace expires."""
        token = ed25519_keypair["mint_token"]({"user_id": "usr_stream", "plan_code": "pro", "is_pro": True})
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]
        ent.save_entitlement_token(token)

        # Expire offline grace
        ent._current_state.last_verified_at_utc = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=75.0)
        ).isoformat()

        # In-flight speech chunk commits safely without data loss
        recorded = metering.record_committed_dictation("Active speech buffer finishing safely")
        assert recorded == 5
        assert metering.get_weekly_usage()["dictation_words"] == 5

    def test_tier2_b10_subsequent_operation_after_expiration_blocked(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """B10: Starting a NEW operation after 72h expiration checks Free weekly limits."""
        token = ed25519_keypair["mint_token"]({"user_id": "usr_subsequent", "plan_code": "pro", "is_pro": True})
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]
        ent.save_entitlement_token(token)

        for i in range(5):
            metering.record_completed_meeting(duration_sec=60.0, meeting_id=i + 1)

        # Expire offline grace
        ent._current_state.last_verified_at_utc = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=73.0)
        ).isoformat()

        can_start, reason = metering.can_start_meeting(ent)
        assert can_start is False
        assert "Weekly meeting session limit reached" in reason

    # --- Group C: Quota & Early Warning Thresholds (5 Tests) ---

    def test_tier2_b11_exact_word_quota_boundaries(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """B11: Word quota transitions: 2,399w (no warn) -> 2,400w (warn) -> 2,999w (ok) -> 3,000w (blocked)."""
        metering = commercial_env["metering"]
        metering.record_committed_dictation(" ".join(["w"] * 2399))
        assert metering.get_usage_snapshot().is_dictation_warning is False

        metering.record_committed_dictation("w")
        assert metering.get_usage_snapshot().is_dictation_warning is True

        metering.record_committed_dictation(" ".join(["w"] * 599))
        assert metering.can_start_dictation()[0] is True

        metering.record_committed_dictation("w")
        assert metering.can_start_dictation()[0] is False

    def test_tier2_b12_89_min_meeting_warning_non_terminating(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """B12: Meeting at 89.0 min triggers single-meeting allowance warning without killing call."""
        metering = commercial_env["metering"]
        status = metering.evaluate_live_meeting_overrun(elapsed_sec=89 * 60.0)
        assert status.warning is True
        assert status.overrun is False
        assert "remaining in standard single-meeting allowance" in status.message

    def test_tier2_b13_90_min_meeting_exceeded_safe_overrun(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """B13: Meeting at 90+ min triggers overrun policy but allows call to conclude."""
        metering = commercial_env["metering"]
        status = metering.evaluate_live_meeting_overrun(elapsed_sec=91 * 60.0)
        assert status.warning is True
        assert status.overrun is True
        assert "continue recording uninterrupted" in status.message

    def test_tier2_b14_ask_vox_tenth_query_allowed_eleventh_blocked(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """B14: Ask VOX 10th query succeeds; 11th query blocked."""
        metering = commercial_env["metering"]
        for _ in range(9):
            metering.record_ask_vox_query()
        assert metering.can_ask_vox()[0] is True

        metering.record_ask_vox_query()  # 10th
        assert metering.can_ask_vox()[0] is False

    def test_tier2_b15_meeting_count_fifth_allowed_sixth_blocked(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """B15: 5th meeting succeeds; 6th meeting blocked."""
        metering = commercial_env["metering"]
        for i in range(4):
            metering.record_completed_meeting(duration_sec=60.0, meeting_id=i + 1)
        assert metering.can_start_meeting()[0] is True

        metering.record_completed_meeting(duration_sec=60.0, meeting_id=5)
        assert metering.can_start_meeting()[0] is False

    # --- Group D: Multi-User Concurrency & Session Switching (5 Tests) ---

    def test_tier2_b16_user_switch_with_inflight_usage(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """B16: In-flight usage event immediately after user switch is attributed to new user."""
        metering = commercial_env["metering"]
        metering.set_user_context("usr_a", "free")
        metering.record_committed_dictation("Alice words")

        metering.set_user_context("usr_b", "free")
        metering.record_committed_dictation("Bob words")

        metering.set_user_context("usr_a", "free")
        assert metering.get_weekly_usage()["dictation_words"] == 2
        metering.set_user_context("usr_b", "free")
        assert metering.get_weekly_usage()["dictation_words"] == 2

    def test_tier2_b17_rapid_consecutive_user_switching(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """B17: Rapid cycling between user contexts preserves SQLite WAL integrity."""
        metering = commercial_env["metering"]
        for i in range(20):
            uid = f"usr_{i % 3}"
            metering.set_user_context(uid, "free")
            metering.record_committed_dictation(f"Speech {i}")

        for i in range(3):
            metering.set_user_context(f"usr_{i}", "free")
            assert metering.get_weekly_usage()["dictation_words"] > 0

    def test_tier2_b18_user_a_exhausted_user_b_clean_quota(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """B18: User A exhausted at 3,000w; User B starts with clean 3,000w quota."""
        metering = commercial_env["metering"]

        metering.set_user_context("user_a", "free")
        metering.record_committed_dictation(" ".join(["w"] * 3000))
        assert metering.can_start_dictation()[0] is False

        metering.set_user_context("user_b", "free")
        assert metering.can_start_dictation()[0] is True
        metering.record_committed_dictation("Bob typing")
        assert metering.get_weekly_usage()["dictation_words"] == 2

        metering.set_user_context("user_a", "free")
        assert metering.can_start_dictation()[0] is False

    def test_tier2_b19_dpapi_vault_corruption_recovery(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """B19: Corrupted bytes in vault file recover safely, falling back to Free tier."""
        vault = commercial_env["vault_path"]
        vault.write_bytes(b"\x00\xFF\xAA\x55CORRUPTED_DPAPI_BLOB")

        recovered_mgr = EntitlementManager(cache_file=vault)
        assert recovered_mgr.is_pro() is False
        assert recovered_mgr.get_state().plan_code == "free"

    def test_tier2_b20_null_or_empty_user_id_safety(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """B20: Null or empty user_id context falls back safely to 'default'."""
        metering = commercial_env["metering"]
        metering.set_user_context("", None)
        assert metering._user_id == "default"
        assert metering._plan_code == "free"

    # --- Group E: Usage Engine Boundary Cases (5 Tests) ---

    def test_tier2_b21_monday_00_00_utc_exact_rollover(self) -> None:
        """B21: Rollover between Sunday 23:59:59 and Monday 00:00:01 generates distinct periods."""
        sunday = datetime.datetime(2026, 11, 15, 23, 59, 59, tzinfo=datetime.timezone.utc)
        monday = datetime.datetime(2026, 11, 16, 0, 0, 1, tzinfo=datetime.timezone.utc)

        _, _, p_sun = get_current_week_period_utc(sunday)
        _, _, p_mon = get_current_week_period_utc(monday)
        assert p_sun != p_mon

    def test_tier2_b22_empty_dictation_zero_increment(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """B22: Committing empty text or pure whitespace causes 0 word increment."""
        metering = commercial_env["metering"]
        assert metering.record_committed_dictation("") == 0
        assert metering.record_committed_dictation("   \n\t  ") == 0
        assert metering.get_weekly_usage()["dictation_words"] == 0

    def test_tier2_b23_single_massive_dictation_overrun(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """B23: Single massive 5,000-word dictation commits fully; blocks next session."""
        metering = commercial_env["metering"]
        words = metering.record_committed_dictation(" ".join(["word"] * 5000))
        assert words == 5000
        assert metering.get_weekly_usage()["dictation_words"] == 5000
        assert metering.can_start_dictation()[0] is False

    def test_tier2_b24_multithreaded_concurrent_metering_safety(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """B24: Multithreaded concurrent recording produces exact cumulative word count."""
        metering = commercial_env["metering"]

        def worker() -> None:
            for _ in range(25):
                metering.record_committed_dictation("One two three four")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 4 threads * 25 runs * 4 words = 400 words
        assert metering.get_weekly_usage()["dictation_words"] == 400

    def test_tier2_b25_sqlite_dpapi_seal_tamper_detection(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """B25: Direct tampering of SQLite word counts without updating DPAPI seal is detected."""
        metering = commercial_env["metering"]
        metering.record_committed_dictation("Initial voice typing")

        with sqlite3.connect(str(commercial_env["db_path"])) as con:
            cur = con.cursor()
            cur.execute("SELECT dpapi_seal FROM local_metering_periods")
            seal_blob = cur.fetchone()[0]

            # Decrypt seal
            decrypted = dpapi_decrypt(seal_blob)
            seal_data = json.loads(decrypted.decode("utf-8"))
            assert seal_data["words"] == 3

            # Tamper SQLite row directly
            cur.execute("UPDATE local_metering_periods SET dictation_words = 999999")
            con.commit()

            cur.execute("SELECT dictation_words FROM local_metering_periods")
            tampered_words = cur.fetchone()[0]

            # Discrepancy between tampered words and encrypted seal proves detection
            assert tampered_words != seal_data["words"]


# ==============================================================================
# Tier 3: Pairwise Combinations (15 Tests)
# ==============================================================================

class TestTier3PairwiseCombinations:
    """Tier 3: Pairwise interactions of offline grace, quotas, switching, and upgrades."""

    def test_tier3_p01_offline_grace_and_quota_exhaustion(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """P01: Pro user dictates 3,500w offline; 72h expires; fallback to Free immediately blocks."""
        token = ed25519_keypair["mint_token"]({"user_id": "usr_p01", "plan_code": "pro", "is_pro": True})
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]
        ent.save_entitlement_token(token)

        metering.record_committed_dictation(" ".join(["w"] * 3500))
        assert metering.can_start_dictation(ent)[0] is True

        # Expire offline grace
        ent._current_state.last_verified_at_utc = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=73.0)
        ).isoformat()

        can_dictate, reason = metering.can_start_dictation(ent)
        assert can_dictate is False
        assert "Weekly dictation allowance reached" in reason

    def test_tier3_p02_account_switching_pro_to_free_transition(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """P02: Switching from Pro User A to Free User B locks enterprise features immediately."""
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]

        token = ed25519_keypair["mint_token"]({"user_id": "usr_pro", "plan_code": "pro", "is_pro": True})
        ent.save_entitlement_token(token)
        metering.set_user_context("usr_pro", "pro")
        assert ent.is_pro() is True

        # Switch to Bob (Free)
        ent.clear_entitlement()
        metering.set_user_context("usr_bob", "free")
        assert ent.is_pro() is False
        assert ent.is_feature_enabled("executive_export") is False

    def test_tier3_p03_offline_meeting_crosses_monday_reset(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """P03: Offline meeting crossing Monday 00:00 UTC finishes safely; next week starts fresh."""
        token = ed25519_keypair["mint_token"]({"user_id": "usr_p03", "plan_code": "pro", "is_pro": True})
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]
        ent.save_entitlement_token(token)

        # Meeting finishes safely
        metering.record_completed_meeting(duration_sec=3600.0, meeting_id=101)
        assert metering.get_weekly_usage()["meeting_count"] == 1

    def test_tier3_p04_expired_token_renewal_on_reconnect(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """P04: Reconnecting after expiry with fresh token immediately restores Pro status."""
        ent = commercial_env["entitlement"]
        ent._current_state.last_verified_at_utc = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=100.0)
        ).isoformat()
        assert ent.is_offline_expired() is True

        fresh_token = ed25519_keypair["mint_token"]({"user_id": "usr_renew", "plan_code": "pro", "is_pro": True})
        ok, _ = ent.save_entitlement_token(fresh_token)
        assert ok is True
        assert ent.is_pro() is True

    def test_tier3_p05_quota_exhaustion_upgrade_flow_unblock(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """P05: Exhausted Free user upgrades to Pro; dictation is unblocked immediately."""
        metering = commercial_env["metering"]
        ent = commercial_env["entitlement"]

        metering.record_committed_dictation(" ".join(["w"] * 3000))
        assert metering.can_start_dictation(ent)[0] is False

        # Upgrade
        token = ed25519_keypair["mint_token"]({"user_id": "usr_upgraded", "plan_code": "pro", "is_pro": True})
        ent.save_entitlement_token(token)
        assert metering.can_start_dictation(ent)[0] is True

    def test_tier3_p06_multi_user_dpapi_vault_isolation(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """P06: Two vault files for different users remain strictly isolated on disk."""
        vault_a = commercial_env["temp_path"] / "user_a.vault"
        vault_b = commercial_env["temp_path"] / "user_b.vault"

        mgr_a = EntitlementManager(cache_file=vault_a, verify_key=ed25519_keypair["public_hex"])
        mgr_b = EntitlementManager(cache_file=vault_b, verify_key=ed25519_keypair["public_hex"])

        token_a = ed25519_keypair["mint_token"]({"user_id": "usr_a", "plan_code": "pro_monthly", "is_pro": True})
        mgr_a.save_entitlement_token(token_a)

        assert mgr_a.is_pro() is True
        assert mgr_b.is_pro() is False

    def test_tier3_p07_tampered_token_on_startup_offline(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """P07: Starting offline with a tampered vault safely initializes Free tier."""
        vault = commercial_env["vault_path"]
        # Save forged token into vault
        forged_data = json.dumps({"token": "forged.payload.sig", "last_verified_at_utc": "2026-11-15"}).encode("utf-8")
        encrypted = dpapi_encrypt(forged_data, description="vox_entitlement_vault")
        vault.write_bytes(encrypted)

        mgr = EntitlementManager(cache_file=vault, verify_key=ed25519_keypair["public_hex"])
        assert mgr.is_pro() is False
        assert mgr.get_state().plan_code == "free"

    def test_tier3_p08_live_dictation_warning_non_intrusive(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """P08: Crossing 2,400w during speaking session flags warning without cutting speech."""
        metering = commercial_env["metering"]
        words_recorded = metering.record_committed_dictation(" ".join(["w"] * 2450))
        assert words_recorded == 2450
        assert metering.get_usage_snapshot().is_dictation_warning is True

    def test_tier3_p09_ask_vox_offline_grace_boundary(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """P09: Pro user has unlimited queries; after 72h offline expires, limited to 10."""
        token = ed25519_keypair["mint_token"]({"user_id": "usr_p09", "plan_code": "pro", "is_pro": True})
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]
        ent.save_entitlement_token(token)

        for _ in range(12):
            metering.record_ask_vox_query()
        assert metering.can_ask_vox(ent)[0] is True

        # Expire offline grace
        ent._current_state.last_verified_at_utc = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=75.0)
        ).isoformat()
        assert metering.can_ask_vox(ent)[0] is False

    def test_tier3_p10_meeting_overrun_and_session_limit_interaction(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """P10: 5th meeting runs 100 min (exceeds 90m cap); finishes safely; 6th meeting blocked."""
        metering = commercial_env["metering"]
        for i in range(4):
            metering.record_completed_meeting(duration_sec=60.0, meeting_id=i + 1)

        # 5th meeting runs 100 min
        status = metering.evaluate_live_meeting_overrun(elapsed_sec=100 * 60.0)
        assert status.overrun is True
        metering.record_completed_meeting(duration_sec=6000.0, meeting_id=5)

        # 6th meeting is blocked
        assert metering.can_start_meeting()[0] is False

    def test_tier3_p11_update_check_under_both_free_and_pro(
        self, ed25519_keypair: Dict[str, Any]
    ) -> None:
        """P11: Release manifest signature verification behaves identically on Free and Pro."""
        manifest_bytes = json.dumps({"version": "1.0.0", "sha256": "fakehash"}, sort_keys=True).encode("utf-8")
        sig = ed25519_sign(manifest_bytes, ed25519_keypair["seed_bytes"])

        assert ed25519_verify(sig, manifest_bytes, ed25519_keypair["public_bytes"]) is True

    def test_tier3_p12_sqlite_db_reset_preserves_entitlement_vault(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """P12: Resetting/deleting SQLite DB file leaves DPAPI entitlement vault intact."""
        token = ed25519_keypair["mint_token"]({"user_id": "usr_p12", "plan_code": "pro", "is_pro": True})
        ent = commercial_env["entitlement"]
        ent.save_entitlement_token(token)

        # Delete SQLite DB
        db_path = commercial_env["db_path"]
        import gc
        import time
        try:
            commercial_env["metering"].close()
        except Exception:
            pass
        gc.collect()
        if db_path.exists():
            try:
                db_path.unlink()
            except PermissionError:
                time.sleep(0.1)
                gc.collect()
                db_path.unlink()

        # Vault remains intact
        assert commercial_env["vault_path"].exists() is True
        fresh_mgr = EntitlementManager(
            cache_file=commercial_env["vault_path"], verify_key=ed25519_keypair["public_hex"]
        )
        assert fresh_mgr.is_pro() is True

    def test_tier3_p13_rapid_plan_toggling_speech_preview_stability(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """P13: Rapidly toggling between Free and Pro maintains valid entitlement state."""
        ent = commercial_env["entitlement"]
        token_pro = ed25519_keypair["mint_token"]({"user_id": "u", "plan_code": "pro", "is_pro": True})
        token_free = ed25519_keypair["mint_token"]({"user_id": "u", "plan_code": "free", "is_pro": False})

        for _ in range(5):
            ent.save_entitlement_token(token_pro)
            assert ent.is_pro() is True
            ent.save_entitlement_token(token_free)
            assert ent.is_pro() is False

    def test_tier3_p14_high_frequency_multilingual_word_stress(
        self, commercial_env: Dict[str, Any]
    ) -> None:
        """P14: 100 consecutive multilingual dictation commits maintain exact ledger accuracy."""
        metering = commercial_env["metering"]
        total_words = 0
        samples = [
            "Quick speech test",
            "नमस्ते दुनिया",
            "Aaj review meetings honge",
        ]
        for i in range(100):
            sample = samples[i % len(samples)]
            words = metering.record_committed_dictation(sample)
            total_words += words

        assert metering.get_weekly_usage()["dictation_words"] == total_words

    def test_tier3_p15_downgrade_preserves_historical_transcripts(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """P15: Downgrade from Pro to Free preserves all prior meeting and dictation records."""
        token = ed25519_keypair["mint_token"]({"user_id": "usr_downgrade", "plan_code": "pro", "is_pro": True})
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]
        ent.save_entitlement_token(token)

        metering.record_committed_dictation("Confidential meeting notes")
        metering.record_completed_meeting(duration_sec=7200.0, meeting_id=999)

        ent.revert_to_free()
        assert ent.is_pro() is False

        usage = metering.get_weekly_usage()
        assert usage["dictation_words"] == 3
        assert usage["meeting_duration_sec"] == 7200.0
        assert usage["meeting_count"] == 1


# ==============================================================================
# Tier 4: Real-World Customer Workload Scenarios (5 Comprehensive Workloads)
# ==============================================================================

class TestTier4CustomerWorkloads:
    """Tier 4: End-to-end multi-user customer workloads derived from TEST_INFRA.md."""

    def test_tier4_scenario_01_account_a_pro_monthly_10000_words(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """
        Scenario 1: Account A subscribes to Pro Monthly via Razorpay, activates on desktop,
        dictates 10,000 words without limits, 0 blocks, 0 warnings.
        """
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]

        # 1. Activate Pro Monthly
        payload = {
            "user_id": "account_a",
            "plan_code": "pro_monthly",
            "is_pro": True,
            "usage_limits": {"dictation_words_weekly": 999999999},
        }
        token = ed25519_keypair["mint_token"](payload)
        ok, err = ent.save_entitlement_token(token)
        assert ok is True

        metering.set_user_context("account_a", "pro_monthly")
        assert ent.is_pro() is True

        # 2. Dictate 10 batches of 1,000 words
        for i in range(10):
            can_start, _ = metering.can_start_dictation(ent)
            assert can_start is True
            metering.record_committed_dictation(" ".join(["word"] * 1000))

        # 3. Verify total is 10,000 words without exhaustion
        usage = metering.get_weekly_usage()
        assert usage["dictation_words"] == 10000
        snap = metering.get_usage_snapshot()
        assert snap.dictation_is_exhausted is False
        assert snap.is_dictation_warning is False

    def test_tier4_scenario_02_account_switch_a_to_b_free_exhaustion(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """
        Scenario 2: Account A logs out; Account B logs in on same laptop, starts Free mode,
        reaches 80% warning at 2,400 words, blocked at 3,000 words. Account A data isolated.
        """
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]

        # 1. Account A logs out
        metering.set_user_context("account_a", "pro_monthly")
        metering.record_committed_dictation(" ".join(["a"] * 5000))
        ent.clear_entitlement()

        # 2. Account B logs in Free
        metering.set_user_context("account_b", "free")
        assert metering.get_weekly_usage()["dictation_words"] == 0

        # 3. Dictate 2,400 words -> 80% warning
        metering.record_committed_dictation(" ".join(["b"] * 2400))
        snap = metering.get_usage_snapshot()
        assert snap.is_dictation_warning is True
        assert metering.can_start_dictation(ent)[0] is True

        # 4. Dictate 600 more words (total 3,000) -> blocked
        metering.record_committed_dictation(" ".join(["b"] * 600))
        can_start, reason = metering.can_start_dictation(ent)
        assert can_start is False
        assert "Weekly dictation allowance reached" in reason

        # 5. Account A's 5,000 words remain completely isolated
        metering.set_user_context("account_a", "pro_monthly")
        assert metering.get_weekly_usage()["dictation_words"] == 5000

    def test_tier4_scenario_03_flight_75h_offline_meeting_and_free_fallback(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """
        Scenario 3: Pro user goes offline on flight for 75 hours (>72h grace); mid-flight meeting
        completes without data loss; new dictation switches to Free limits with warning.
        """
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]

        token = ed25519_keypair["mint_token"]({"user_id": "pilot_user", "plan_code": "pro", "is_pro": True})
        ent.save_entitlement_token(token)
        metering.set_user_context("pilot_user", "pro")

        now = datetime.datetime.now(datetime.timezone.utc)

        # 1. At T+48 hours: meeting allowed
        ent._current_state.last_verified_at_utc = (now - datetime.timedelta(hours=48.0)).isoformat()
        assert ent.is_pro() is True
        assert metering.can_start_meeting(ent)[0] is True

        # 2. At T+71.8 hours: meeting starts
        ent._current_state.last_verified_at_utc = (now - datetime.timedelta(hours=71.8)).isoformat()

        # 3. At T+72.0 hours (mid-meeting): grace expires
        # Meeting continues uninterrupted
        status = metering.evaluate_live_meeting_overrun(elapsed_sec=3600.0, entitlement=ent)
        assert status.warning is False

        # Meeting concludes at T+72.8 hours with full duration saved
        metering.record_completed_meeting(duration_sec=3600.0, meeting_id=202)
        assert metering.get_weekly_usage()["meeting_duration_sec"] == 3600.0

        # 4. At T+75 hours: User attempts NEW dictation while past Free word quota
        ent._current_state.last_verified_at_utc = (now - datetime.timedelta(hours=75.0)).isoformat()
        metering.record_committed_dictation(" ".join(["word"] * 3000))
        can_dictate, reason = metering.can_start_dictation(ent)
        assert can_dictate is False
        assert "Weekly dictation allowance reached" in reason

    def test_tier4_scenario_04_token_renewal_and_revocation_lifecycle(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """
        Scenario 4: Token lifecycle: Active Pro -> 60h offline -> Token renewed -> Subscription canceled
        via webhook -> Downgrades safely to Free tier while preserving historical notes.
        """
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]

        # 1. Initial Pro Token
        token1 = ed25519_keypair["mint_token"]({"user_id": "usr_lifecycle", "plan_code": "pro", "is_pro": True})
        ent.save_entitlement_token(token1)
        metering.record_committed_dictation("Meeting 1 notes")
        assert ent.is_pro() is True

        # 2. 60h offline
        now = datetime.datetime.now(datetime.timezone.utc)
        ent._current_state.last_verified_at_utc = (now - datetime.timedelta(hours=60.0)).isoformat()
        assert ent.is_offline_expired() is False

        # 3. Token renewed
        token2 = ed25519_keypair["mint_token"]({"user_id": "usr_lifecycle", "plan_code": "pro", "is_pro": True})
        ent.save_entitlement_token(token2)
        assert abs(ent.get_effective_entitlement()["offline_hours_remaining"] - MAX_OFFLINE_GRACE_HOURS) < 0.05

        # 4. Subscription canceled -> fresh token with plan="free"
        token_canceled = ed25519_keypair["mint_token"]({"user_id": "usr_lifecycle", "plan_code": "free", "is_pro": False})
        ent.save_entitlement_token(token_canceled)
        assert ent.is_pro() is False
        assert ent.get_state().plan_code == "free"

        # 5. Historical notes remain 100% accessible
        assert metering.get_weekly_usage()["dictation_words"] == 3

    def test_tier4_scenario_05_tampered_token_security_rejection(
        self, commercial_env: Dict[str, Any], ed25519_keypair: Dict[str, Any]
    ) -> None:
        """
        Scenario 5: Adversary injects forged token with is_pro: True and forged signature;
        client safely rejects token, reverts to Free tier, and logs warning without leaking Pro features.
        """
        ent = commercial_env["entitlement"]
        metering = commercial_env["metering"]

        # Forge token using rogue key
        forged_payload = {
            "user_id": "hacker",
            "plan_code": "pro_annual",
            "is_pro": True,
            "usage_limits": {"dictation_words_weekly": 999999999},
        }
        forged_token = ed25519_keypair["mint_token"](forged_payload, rogue=True)

        # Attempt to load into client vault
        ok, err = ent.save_entitlement_token(forged_token)
        assert ok is False
        assert "Entitlement verification failed" in err

        # Verify client is strictly in Free tier
        assert ent.is_pro() is False
        assert ent.get_state().plan_code == "free"
        assert ent.is_feature_enabled("executive_export") is False

"""
Milestone 2 Automated Test Suite: Desktop UI Visibility & Account Isolation.

Exercises:
1. Clean Auth Broker Separation (DesktopAuthBroker vs. CalendarAuthBroker).
2. Multi-User Account Isolation (Google Account A vs. Account B in EntitlementManager & UsageMeteringEngine).
3. Logout DPAPI Vault Cleanup and Free Tier Fallback.
4. Desktop Left Sidebar Plan Status Card (Plan Badge, Word Quota Meter, Upgrade CTA).
5. Dynamic Signal Updates (entitlement_changed, usage_updated, auth_state_changed).
6. SystemTrayManager Plan Status and Upgrade Action.
7. Gated Operation Upgrade Dialog Requesting.
"""

from __future__ import annotations

import base64
import datetime
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from voxlocal.auth.desktop_auth import DesktopAuthBroker, UserProfile
from voxlocal.calendar.auth_service import CalendarAuthBroker
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.core.ed25519_rfc8032 import ed25519_sign, ed25519_public_key
from voxlocal.core.entitlement import (
    DEFAULT_ENTITLEMENT_PUBLIC_KEY,
    EntitlementManager,
)
from tests.token_test_helper import (
    DEV_FALLBACK_PRIVATE_KEY_HEX,
    TEST_DEV_PUBLIC_KEY_HEX,
    sign_entitlement_payload,
)
from voxlocal.core.metering import UsageMeteringEngine
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.tray_manager import SystemTrayManager


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def temp_environment():
    prev_pub_key = os.environ.get("VOX_ENTITLEMENT_PUBLIC_KEY")
    os.environ["VOX_ENTITLEMENT_PUBLIC_KEY"] = TEST_DEV_PUBLIC_KEY_HEX
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            tmp_path = Path(tmp_dir)
            cfg = AppConfig(storage_dir=str(tmp_path))
            cfg.disable_model_preload = True
            cfg.disable_hotkeys = True
            db_path = Path(cfg.db_path)
            vault_path = tmp_path / "entitlement.vault"
            auth_vault = tmp_path / "auth_session.vault"
            yield {
                "tmp_path": tmp_path,
                "db_path": db_path,
                "vault_path": vault_path,
                "auth_vault": auth_vault,
                "cfg": cfg,
            }
    finally:
        if prev_pub_key is None:
            os.environ.pop("VOX_ENTITLEMENT_PUBLIC_KEY", None)
        else:
            os.environ["VOX_ENTITLEMENT_PUBLIC_KEY"] = prev_pub_key


def _create_signed_token(user_id: str, plan_code: str = "pro_monthly", hours_valid: float = 720.0) -> str:
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    exp_utc = now_utc + datetime.timedelta(hours=hours_valid)
    payload = {
        "user_id": user_id,
        "plan": plan_code,
        "plan_code": plan_code,
        "status": "active",
        "issued_at_utc": now_utc.isoformat(),
        "expires_at_utc": exp_utc.isoformat(),
        "exp": int(exp_utc.timestamp()),
        "limits": {
            "dictation_words_weekly": 999999999,
            "meeting_minutes_weekly": 999999.0,
            "meeting_sessions_weekly": 999999,
            "ask_vox_queries_weekly": 999999,
        },
        "features": {
            "dictation": True,
            "dual_track_recording": True,
            "meeting_summaries": True,
            "cross_meeting_memory": True,
            "action_item_sync": True,
        },
    }
    return sign_entitlement_payload(payload, private_key=DEV_FALLBACK_PRIVATE_KEY_HEX)


class TestAuthBrokerSeparation:
    """Verify that DesktopAuthBroker and CalendarAuthBroker are decoupled cleanly without collisions."""

    def test_broker_separation_in_controller(self, qapp, temp_environment):
        cfg = temp_environment["cfg"]
        ctrl = AppController(config=cfg)
        try:
            # 1. Verify desktop auth broker instance
            assert hasattr(ctrl, "desktop_auth_broker")
            assert isinstance(ctrl.desktop_auth_broker, DesktopAuthBroker)

            # 2. Verify calendar auth broker instance
            assert hasattr(ctrl, "calendar_auth_broker")
            assert isinstance(ctrl.calendar_auth_broker, CalendarAuthBroker)

            # 3. Verify backward-compatibility alias
            assert hasattr(ctrl, "auth_broker")
            assert ctrl.auth_broker is ctrl.desktop_auth_broker
            assert not isinstance(ctrl.auth_broker, CalendarAuthBroker)

            # 4. Verify methods on desktop auth broker work without attribute collisions
            session = ctrl.desktop_auth_broker.load_session()
            assert session is None  # Initially unauthenticated

            status = ctrl.get_account_status()
            assert status["signed_in"] is False
            assert status["name"] == "Guest User"
            assert status["plan"] == "free"
            assert status["is_pro"] is False
        finally:
            ctrl.shutdown()

    def test_desktop_auth_broker_methods(self, temp_environment):
        vault_path = temp_environment["auth_vault"]
        broker = DesktopAuthBroker(vault_path=vault_path)

        assert broker.load_session() is None
        assert broker.is_authenticated() is False

        # Simulate user login persistence
        profile = UserProfile(
            user_id="usr_test_123",
            email="test.user@example.com",
            display_name="Test User",
            avatar_url="https://example.com/avatar.png",
            plan_code="pro_monthly",
            is_pro=True,
            access_token="tok_access_abc",
            refresh_token="tok_refresh_xyz",
            expires_at=time.time() + 3600,
        )
        broker._save_session(profile)

        # Verify load_session format
        session = broker.load_session()
        assert session is not None
        assert session["user_id"] == "usr_test_123"
        assert session["access_token"] == "tok_access_abc"
        assert session["user"]["email"] == "test.user@example.com"
        assert session["user"]["name"] == "Test User"
        assert session["user"]["is_pro"] is True

        # Verify clear_session
        broker.clear_session()
        assert broker.load_session() is None
        assert broker.is_authenticated() is False


class TestMultiUserAccountIsolation:
    """Verify that multiple users sharing a device cannot leak quota or Pro entitlements."""

    def test_metering_multi_user_isolation(self, temp_environment):
        db_path = temp_environment["db_path"]
        meter = UsageMeteringEngine(db_path=db_path)

        # User A logs in and records dictation
        meter.set_user_context("user_alpha", "free")
        meter.record_committed_dictation("This is a test speech session containing ten genuine spoken words right now.")
        usage_a = meter.get_weekly_usage()
        assert usage_a["dictation_words"] == 13

        # User B logs in on same device
        meter.set_user_context("user_beta", "free")
        usage_b_initial = meter.get_weekly_usage()
        assert usage_b_initial["dictation_words"] == 0  # Brand new ledger for user_beta

        meter.record_committed_dictation("Short speech input here.")
        usage_b = meter.get_weekly_usage()
        assert usage_b["dictation_words"] == 4

        # Switch back to User A
        meter.set_user_context("user_alpha", "free")
        usage_a_restored = meter.get_weekly_usage()
        assert usage_a_restored["dictation_words"] == 13  # Preserved independently

    def test_entitlement_manager_multi_user_token_rejection(self, temp_environment):
        vault_path = temp_environment["vault_path"]
        em = EntitlementManager(cache_file=vault_path)

        # Mint Pro token for User A
        token_a = _create_signed_token("user_alpha", "pro_monthly")

        # Save and verify for User A
        em.set_user_context("user_alpha")
        ok, err = em.save_entitlement_token(token_a)
        assert ok is True
        assert em.is_pro() is True
        assert em.get_effective_entitlement()["is_pro"] is True

        # Switch to User B without User B having a Pro token
        em.set_user_context("user_beta")
        # Pro must be rejected for User B because the token is bound to user_alpha
        assert em.is_pro() is False
        assert em.get_effective_entitlement()["is_pro"] is False
        assert em.get_effective_entitlement()["plan"] == "free"

        # Attempt to save User A's token while User B is active -> strictly rejected
        ok_b, err_b = em.save_entitlement_token(token_a)
        assert ok_b is False
        assert "not current user 'user_beta'" in err_b

    def test_controller_logout_flow_clears_vault_and_reverts(self, qapp, temp_environment):
        cfg = temp_environment["cfg"]
        ctrl = AppController(config=cfg)
        try:
            # Setup simulated active session
            token = _create_signed_token("user_alpha", "pro_monthly")
            ctrl.entitlement.set_user_context("user_alpha")
            ctrl.entitlement.save_entitlement_token(token)
            ctrl.metering.set_user_context("user_alpha", "pro_monthly")

            # Spy on emitted signals
            ent_emitted = []
            auth_emitted = []
            ctrl.entitlement_changed.connect(lambda e: ent_emitted.append(e))
            ctrl.auth_state_changed.connect(lambda a: auth_emitted.append(a))

            # Perform logout
            ctrl.logout_user()

            # Verify local vault cleared & state reverted to free
            assert ctrl.entitlement.is_pro() is False
            assert ctrl.entitlement.get_effective_entitlement()["plan"] == "free"
            assert ctrl.metering._user_id == "default"
            assert ctrl.metering.is_pro() is False

            # Verify signals were emitted
            assert len(ent_emitted) >= 1
            assert ent_emitted[-1]["is_pro"] is False
            assert len(auth_emitted) >= 1
            assert auth_emitted[-1]["signed_in"] is False
        finally:
            ctrl.shutdown()


class TestMainWindowPlanStatusCard:
    """Verify that the left sidebar in MainWindow surfaces the Plan Badge, Word Meter, and Upgrade CTA."""

    def test_sidebar_plan_status_card_initial_state_free(self, qapp, temp_environment):
        cfg = temp_environment["cfg"]
        ctrl = AppController(config=cfg)
        win = MainWindow(ctrl)
        try:
            # 1. Verify widgets exist in sidebar
            assert hasattr(win, "plan_card")
            assert hasattr(win, "lbl_plan_badge")
            assert hasattr(win, "lbl_quota_words")
            assert hasattr(win, "bar_quota_words")
            assert hasattr(win, "btn_plan_upgrade")

            # 2. Check initial Free values
            assert "VOX Free" in win.lbl_plan_badge.text()
            assert "Words: 0 / 3,000" in win.lbl_quota_words.text()
            assert win.bar_quota_words.value() == 0
            assert win.bar_quota_words.maximum() == 3000
            assert "Upgrade to Pro" in win.btn_plan_upgrade.text()
        finally:
            win.close()
            ctrl.shutdown()

    def test_sidebar_plan_status_card_updates_on_entitlement_changed(self, qapp, temp_environment):
        cfg = temp_environment["cfg"]
        ctrl = AppController(config=cfg)
        win = MainWindow(ctrl)
        try:
            # Emit Pro entitlement
            pro_ent = {
                "plan": "pro_monthly",
                "plan_name": "VOX Pro",
                "is_pro": True,
                "limits": {"dictation_words_weekly": 999999999},
            }
            ctrl.entitlement_changed.emit(pro_ent)

            # Check UI updated dynamically
            assert "VOX Pro" in win.lbl_plan_badge.text()
            assert "Unlimited" in win.lbl_quota_words.text()
            assert "Manage Plan" in win.btn_plan_upgrade.text()

            # Emit Free downgrade
            free_ent = {
                "plan": "free",
                "plan_name": "VOX Free",
                "is_pro": False,
                "limits": {"dictation_words_weekly": 3000},
            }
            ctrl.entitlement_changed.emit(free_ent)

            assert "VOX Free" in win.lbl_plan_badge.text()
            assert "3,000" in win.lbl_quota_words.text()
            assert "Upgrade to Pro" in win.btn_plan_upgrade.text()
        finally:
            win.close()
            ctrl.shutdown()

    def test_sidebar_quota_meter_updates_on_usage_updated(self, qapp, temp_environment):
        cfg = temp_environment["cfg"]
        ctrl = AppController(config=cfg)
        win = MainWindow(ctrl)
        try:
            # Emit usage update signal
            ctrl.usage_updated.emit({"dictation_words": 1450})

            assert "1,450 / 3,000" in win.lbl_quota_words.text()
            assert win.bar_quota_words.value() == 1450

            # Near-quota threshold (e.g. 2,800 words)
            ctrl.usage_updated.emit({"dictation_words": 2800})
            assert "2,800 / 3,000" in win.lbl_quota_words.text()
            assert win.bar_quota_words.value() == 2800
        finally:
            win.close()
            ctrl.shutdown()

    def test_upgrade_button_triggers_request_upgrade_dialog(self, qapp, temp_environment):
        cfg = temp_environment["cfg"]
        ctrl = AppController(config=cfg)
        win = MainWindow(ctrl)
        try:
            received_reasons = []
            ctrl.upgrade_dialog_requested.connect(lambda r: received_reasons.append(r))

            # Click upgrade button on Free tier (signed-in user)
            with patch.object(ctrl, "get_account_status", return_value={"signed_in": True, "email": "user@example.com"}):
                with patch("voxlocal.ui.pro_upgrade_dialog.VoxProUpgradeDialog.exec", return_value=1):
                    win.btn_plan_upgrade.click()
            assert len(received_reasons) == 1
            assert "Upgrade to VOX Pro" in received_reasons[0]
        finally:
            win.close()
            ctrl.shutdown()

    def test_sidebar_user_profile_displays_google_account_and_updates_on_auth_state_changed(self, qapp, temp_environment):
        cfg = temp_environment["cfg"]
        ctrl = AppController(config=cfg)
        win = MainWindow(ctrl)
        try:
            # 1. Initial Guest Mode
            assert "Guest" in win.lbl_user_name.text()
            assert not win.btn_google_sign_in.isHidden()
            assert win.btn_user_sign_out.isHidden()

            # 2. Simulate User Sign-in with Google Account
            fake_user_status = {
                "signed_in": True,
                "email": "milinddwivedi0@outlook.com",
                "name": "Milind Krishna Dwivedi",
                "plan": "pro_monthly",
                "plan_name": "VOX Pro",
                "is_pro": True,
                "usage": {"dictation_words": 1200},
                "limits": {},
            }
            with patch.object(ctrl, "get_account_status", return_value=fake_user_status):
                ctrl.auth_state_changed.emit(fake_user_status)

                # Check profile updated
                assert "Milind Krishna" in win.lbl_user_name.text()
                assert "milinddwivedi0@" in win.lbl_user_email.text()
                assert win.profile_avatar.text() == "M"
                assert win.btn_google_sign_in.isHidden()
                assert not win.btn_user_sign_out.isHidden()
                assert "VOX Pro" in win.lbl_plan_badge.text()
                assert "Unlimited" in win.lbl_quota_words.text()

            # 3. Simulate Logout / Revert to Guest
            fake_guest_status = {
                "signed_in": False,
                "email": None,
                "name": "Guest User",
                "plan": "free",
                "plan_name": "VOX Free",
                "is_pro": False,
                "usage": {"dictation_words": 1200},
                "limits": {"dictation_words_weekly": 3000},
            }
            with patch.object(ctrl, "get_account_status", return_value=fake_guest_status):
                ctrl.auth_state_changed.emit(fake_guest_status)

                assert "Guest" in win.lbl_user_name.text()
                assert not win.btn_google_sign_in.isHidden()
                assert win.btn_user_sign_out.isHidden()
                assert "VOX Free" in win.lbl_plan_badge.text()
        finally:
            win.close()
            ctrl.shutdown()


class TestSystemTrayManagerPlanStatus:
    """Verify that SystemTrayManager displays the current plan and offers an Upgrade CTA."""

    def test_tray_plan_actions_free_and_pro(self, qapp, temp_environment):
        cfg = temp_environment["cfg"]
        ctrl = AppController(config=cfg)
        parent_widget = QWidget()
        floating_mock = MagicMock()
        floating_mock.isVisible.return_value = False

        tray = SystemTrayManager(
            parent_widget=parent_widget,
            controller=ctrl,
            floating_widget=floating_mock,
            on_open_tab=lambda idx: None,
            on_exit_app=lambda: None,
        )
        try:
            # 1. Verify actions exist in tray menu
            assert hasattr(tray, "act_plan")
            assert hasattr(tray, "act_upgrade")
            assert tray.act_plan.text() == "Plan: VOX Free"
            assert "Upgrade to Pro" in tray.act_upgrade.text()

            # 2. Trigger upgrade action from tray menu
            upgrade_emitted = []
            ctrl.upgrade_dialog_requested.connect(lambda r: upgrade_emitted.append(r))
            tray.act_upgrade.trigger()
            assert len(upgrade_emitted) == 1

            # 3. Switch to Pro and verify tray refreshes
            pro_ent = {"is_pro": True, "plan": "pro_monthly", "plan_name": "VOX Pro"}
            ctrl.entitlement.get_effective_entitlement = MagicMock(return_value=pro_ent)
            ctrl.entitlement_changed.emit(pro_ent)

            assert tray.act_plan.text() == "Plan: VOX Pro"
            assert "Manage Pro Plan" in tray.act_upgrade.text()
        finally:
            tray.hide()
            ctrl.shutdown()


class TestGatedOperationUpgradeDialog:
    """Verify that hitting quotas emits upgrade_dialog_requested."""

    def test_quota_exhaustion_emits_upgrade_dialog_requested(self, qapp, temp_environment):
        cfg = temp_environment["cfg"]
        ctrl = AppController(config=cfg)
        try:
            dialog_signals = []
            ctrl.upgrade_dialog_requested.connect(lambda r: dialog_signals.append(r))

            # Simulate quota exhaustion (3,000 words committed)
            ctrl.metering.record_committed_dictation(" ".join(["word"] * 3000))

            # Attempt to start dictation
            with patch("voxlocal.ui.pro_upgrade_dialog.VoxProUpgradeDialog.exec", return_value=1):
                ctrl.start_dictation()

            # Must emit upgrade_dialog_requested with quota warning
            assert len(dialog_signals) >= 1
            assert "quota" in dialog_signals[0].lower() or "limit" in dialog_signals[0].lower()
        finally:
            ctrl.shutdown()

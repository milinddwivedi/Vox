"""
Unit and Integration Tests for Web-to-Desktop SSO, Pro Upgrade Gating, and Finalization Thread Safety.
"""

import json
import threading
import time
import urllib.parse
import urllib.request
import pytest

from voxlocal.auth.desktop_auth import DesktopAuthBroker, UserProfile


def test_complete_login_from_payload(tmp_path):
    vault = tmp_path / "test_session.vault"
    broker = DesktopAuthBroker(vault_path=vault)

    synced_events = []
    broker.on_session_synced = lambda prof: synced_events.append(prof)

    # 1. Normal user login payload
    payload = {
        "user_id": "usr_abc_123",
        "email": "sarah@company.com",
        "name": "Sarah Connor",
        "avatar_url": "https://example.com/avatar.jpg",
        "plan_code": "free",
        "is_pro": False,
        "access_token": "token_xyz",
    }

    ok, msg, profile = broker.complete_login_from_payload(payload)
    assert ok is True
    assert profile.email == "sarah@company.com"
    assert profile.display_name == "Sarah Connor"
    assert profile.is_pro is False
    assert len(synced_events) == 1

    # Verify session persisted in vault
    user = broker.get_current_user()
    assert user is not None
    assert user.email == "sarah@company.com"

    # 2. Founder developer master bypass
    founder_payload = {
        "user_id": "50d9a219-b06d-4fe9-acb3-7e32bf7cec2f",
        "email": "milinddwivedi0@gmail.com",
        "name": "Milind Krishna Dwivedi",
        "plan_code": "free",  # even if passed free
        "is_pro": False,
    }

    ok, msg, f_profile = broker.complete_login_from_payload(founder_payload)
    assert ok is True
    assert f_profile.email == "milinddwivedi0@gmail.com"
    assert f_profile.is_pro is True
    assert f_profile.plan_code == "pro_annual"
    assert len(synced_events) == 2


def test_loopback_sync_session_http(tmp_path):
    vault = tmp_path / "http_session.vault"
    broker = DesktopAuthBroker(vault_path=vault)

    # Send POST request to active loopback port 8400 or 3000
    synced_payload = {
        "user_id": "usr_loopback_99",
        "email": "alex@loopback.test",
        "name": "Alex Tester",
        "plan_code": "pro_monthly",
        "is_pro": True,
        "access_token": "ephemeral_jwt_token",
    }
    body = json.dumps(synced_payload).encode("utf-8")

    # Probe 8400 or 3000
    success = False
    for port in (8400, 3000):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/sync-session",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    assert data.get("ok") is True
                    assert data.get("email") == "alex@loopback.test"
                    success = True
                    break
        except Exception:
            continue

    assert success is True
    assert broker.get_current_user().email == "alex@loopback.test"

"""
Tests for Connected Accounts Lifecycle, Sync Health State Machine, and UI Resilience.
Verifies that:
- Historical SQLite errors do not resurrect when live provider is healthy.
- Pausing (disabling) an account transitions sync health to 'paused' rather than 'not_connected'.
- Resuming (enabling) an account executes asynchronously without blocking the UI thread.
- Real token expiry vs. transient network offline are accurately distinguished.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from voxlocal.calendar.base import BaseCalendarProvider, CalendarMeeting
from voxlocal.calendar.unified_engine import UnifiedCalendarEngine
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.calendar_page import CalendarPage, _state_display_info


class MockHealthyProvider(BaseCalendarProvider):
    provider_type = "ms_graph"

    def __init__(self, account_email: str = "test@outlook.com"):
        super().__init__()
        self._account_id = f"ms_graph:{account_email}"
        self._email = account_email
        self._authenticated = True
        self._access_token = "valid_token_xyz"

    def is_authenticated(self) -> bool:
        return True

    def authenticate(self) -> bool:
        return True

    def disconnect(self) -> bool:
        self._authenticated = False
        return True

    def get_provider_status(self) -> Dict[str, Any]:
        return {
            "provider_type": self.provider_type,
            "account_email": self._email,
            "account_upn": self._email,
            "display_name": "Test User",
            "is_healthy": True,
            "connection_state": "connected",
            "error_state": None,
            "friendly_error": None,
            "last_sync_at": "2026-11-16T10:00:00+00:00",
        }

    def get_upcoming_meetings(self, window_minutes: int = 43200) -> List[CalendarMeeting]:
        return []


class MockExpiredProvider(BaseCalendarProvider):
    provider_type = "ms_graph"

    def __init__(self, account_email: str = "expired@outlook.com"):
        super().__init__()
        self._account_id = f"ms_graph:{account_email}"
        self._email = account_email
        self._authenticated = False
        self._access_token = None

    def is_authenticated(self) -> bool:
        return False

    def authenticate(self) -> bool:
        return False

    def disconnect(self) -> bool:
        return True

    def get_provider_status(self) -> Dict[str, Any]:
        return {
            "provider_type": self.provider_type,
            "account_email": self._email,
            "account_upn": self._email,
            "display_name": "Expired User",
            "is_healthy": False,
            "connection_state": "expired",
            "error_state": "AUTH_EXPIRED",
            "friendly_error": "Sign-in expired. Please reconnect your account.",
            "last_sync_at": "2026-11-15T10:00:00+00:00",
        }

    def get_upcoming_meetings(self, window_minutes: int = 43200) -> List[CalendarMeeting]:
        return []


@pytest.fixture
def temp_db(tmp_path) -> LocalDatabase:
    db_file = tmp_path / "voxlocal_test.db"
    return LocalDatabase(str(db_file))


@pytest.fixture
def calendar_engine(temp_db) -> UnifiedCalendarEngine:
    engine = UnifiedCalendarEngine(db=temp_db, user_id="test_user")
    return engine


def test_stale_db_error_does_not_corrupt_live_healthy_provider(calendar_engine, temp_db):
    """
    Bug reproduction & fix verification:
    If SQLite contains a historical error from a previous offline/failed state,
    registering an active healthy provider MUST NOT resurrect the old error.
    """
    acc_id = "ms_graph:user@example.com"
    # Seed historical failed state in SQLite
    temp_db.upsert_calendar_account(
        account_id=acc_id,
        provider_type="ms_graph",
        user_id="test_user",
        account_email="user@example.com",
        display_name="User",
        account_kind="personal",
        tenant_id="consumers",
        enabled=True,
        connection_state="failed",
        last_sync_at="2026-11-14T00:00:00Z",
        last_error="Could not reach Microsoft right now. Check your internet connection.",
        error_code="NETWORK_OFFLINE",
        selected_calendars=[],
        available_calendars=[],
    )

    # Engine rehydrates
    calendar_engine.hydrate_from_database()
    accounts_before = calendar_engine.list_accounts()
    assert len(accounts_before) == 1
    # Without live provider, DB state reflects offline
    assert accounts_before[0]["connection_state"] == "offline_cached" or accounts_before[0]["connection_state"] == "disconnected"

    # Register live, healthy provider
    prov = MockHealthyProvider("user@example.com")
    calendar_engine.register_provider(acc_id, prov)

    # Wait for initial registration background sync to complete
    for _ in range(50):
        if not calendar_engine._is_syncing:
            break
        time.sleep(0.02)

    # Inspect snapshot: Live provider MUST override historical error
    accounts_after = calendar_engine.list_accounts()
    assert len(accounts_after) == 1
    snap = accounts_after[0]
    assert snap["is_healthy"] is True
    assert snap["connection_state"] == "connected"
    assert snap["error_code"] == ""
    assert snap["last_error"] == ""

    # Verify overall sync health summary
    summary = calendar_engine.get_sync_health_summary()
    assert summary["state"] in ("healthy", "syncing")
    assert summary["healthy_accounts"] == 1
    assert summary["degraded_accounts"] == 0
    assert len(summary["errors"]) == 0


def test_paused_account_state_machine_and_health_summary(calendar_engine):
    """
    When all accounts are paused (disabled by user):
    - Sync health summary state must be 'paused', NOT 'not_connected'.
    - Account count must report total accounts with paused breakdown.
    """
    prov = MockHealthyProvider("user@example.com")
    acc_id = prov._account_id
    calendar_engine.register_provider(acc_id, prov)

    for _ in range(50):
        if not calendar_engine._is_syncing:
            break
        time.sleep(0.02)

    summary_active = calendar_engine.get_sync_health_summary()
    assert summary_active["state"] in ("healthy", "syncing")
    assert summary_active["connected_accounts"] == 1
    assert summary_active["total_accounts"] == 1
    assert summary_active["paused_accounts"] == 0

    # User pauses account
    ok = calendar_engine.set_account_enabled(acc_id, False)
    assert ok is True

    summary_paused = calendar_engine.get_sync_health_summary()
    assert summary_paused["state"] == "paused"
    assert summary_paused["connected_accounts"] == 0
    assert summary_paused["total_accounts"] == 1
    assert summary_paused["paused_accounts"] == 1
    assert summary_paused["healthy_accounts"] == 0
    assert summary_paused["degraded_accounts"] == 0

    # User re-enables account
    with patch.object(calendar_engine, "sync_account_now") as mock_sync:
        ok_enable = calendar_engine.set_account_enabled(acc_id, True)
        assert ok_enable is True
        time.sleep(0.05)  # Allow daemon thread to launch
        mock_sync.assert_called_once_with(acc_id)


def test_expired_account_triggers_attention_needed(calendar_engine):
    """
    When a provider has AUTH_EXPIRED:
    - Summary state must be 'attention_needed'.
    - Snapshot must report connection_state='expired'.
    """
    prov = MockExpiredProvider("expired@outlook.com")
    acc_id = prov._account_id
    calendar_engine.register_provider(acc_id, prov)

    summary = calendar_engine.get_sync_health_summary()
    assert summary["state"] == "attention_needed"
    assert summary["degraded_accounts"] == 1
    assert summary["healthy_accounts"] == 0

    snap = calendar_engine.list_accounts()[0]
    assert snap["connection_state"] == "expired"
    assert snap["is_healthy"] is False
    assert snap["error_code"] == "AUTH_EXPIRED"


def test_calendar_page_ui_kpi_rendering_with_paused_and_healthy_accounts():
    """
    Verify CalendarPage KPI badges and text for:
    1. Healthy account: 'Healthy', '1', no reconnect needed.
    2. Paused account: 'Paused', '1 Paused', no false reconnect warning.
    """
    _ = QApplication.instance() or QApplication([])

    mock_controller = MagicMock()
    page = CalendarPage(mock_controller)
    page.apply_theme("light")

    # Scenario 1: Healthy
    health_healthy = {
        "state": "healthy",
        "last_sync_at": "2026-11-16T10:00:00+00:00",
        "healthy_accounts": 1,
        "degraded_accounts": 0,
    }
    accounts_healthy = [{
        "account_id": "ms_graph:test@outlook.com",
        "provider_type": "ms_graph",
        "display_name": "Test User",
        "enabled": True,
        "is_authenticated": True,
        "is_healthy": True,
        "connection_state": "connected",
        "last_error": "",
        "selected_calendars": [],
    }]
    page._render_kpis_and_spotlight(accounts_healthy, health_healthy, [])
    assert page.lbl_kpi_accounts.text() == "1"
    assert page.lbl_kpi_health.text() == "Healthy"

    # Scenario 2: Paused
    health_paused = {
        "state": "paused",
        "last_sync_at": "2026-11-16T10:00:00+00:00",
        "healthy_accounts": 0,
        "degraded_accounts": 0,
        "paused_accounts": 1,
    }
    accounts_paused = [{
        "account_id": "ms_graph:test@outlook.com",
        "provider_type": "ms_graph",
        "display_name": "Test User",
        "enabled": False,
        "is_authenticated": True,
        "is_healthy": False,
        "connection_state": "disabled",
        "last_error": "",
        "selected_calendars": [],
    }]
    page._render_kpis_and_spotlight(accounts_paused, health_paused, [])
    assert "Paused" in page.lbl_kpi_accounts.text()
    assert page.lbl_kpi_health.text() == "Paused"
    assert "paused" in page.lbl_kpi_health_sub.text().lower()


def test_state_display_info_mapping():
    """Verify _state_display_info produces clean user-facing labels without scary warnings."""
    # Paused
    lbl, kind = _state_display_info("disabled", is_healthy=False, enabled=False)
    assert lbl == "Paused" and kind == "muted"

    # Healthy Connected
    lbl, kind = _state_display_info("connected", is_healthy=True, enabled=True)
    assert lbl == "Connected" and kind == "ok"

    # Expired
    lbl, kind = _state_display_info("expired", is_healthy=False, enabled=True)
    assert lbl == "Expired — Reconnect" and kind == "warn"

    # Offline Cached
    lbl, kind = _state_display_info("offline_cached", is_healthy=False, enabled=True)
    assert lbl == "Offline (Cached)" and kind == "muted"

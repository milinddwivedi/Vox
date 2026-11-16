"""
Test calendar account enable/disable lifecycle, sync health transitions, and multi-user fallback.
Ensures that when an account is toggled or reconnected, it never flashes 'Attention Needed'
or 'Reconnect Needed', and credentials saved under 'default' are found by authenticated users.
"""

import pytest
from pathlib import Path
from voxlocal.storage.database import LocalDatabase
from voxlocal.calendar.unified_engine import UnifiedCalendarEngine
from voxlocal.calendar.auth_service import CalendarAuthBroker
from voxlocal.calendar.mock_provider import MockCalendarProvider


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_vox.db"
    db = LocalDatabase(db_file)
    return db


def test_calendar_account_multi_user_credential_fallback(temp_db: LocalDatabase):
    # Save account and credentials as 'default'
    account_id = "ms_graph:user@outlook.com"
    temp_db.upsert_calendar_account(
        account_id=account_id,
        provider_type="ms_graph",
        user_id="default",
        account_email="user@outlook.com",
        display_name="User Outlook",
        enabled=True,
        connection_state="connected",
    )
    temp_db.save_calendar_credentials(
        user_id="default",
        account_id=account_id,
        provider_type="ms_graph",
        encrypted_blob=b"encrypted_token_bytes_xyz",
    )

    # Now verify an authenticated user (e.g. 50d9a219...) can load the account and credentials
    auth_user_id = "50d9a219-b06d-4fe9-acb3-7e32bf7cec2f"
    accts = temp_db.list_calendar_accounts(user_id=auth_user_id)
    assert len(accts) == 1
    assert accts[0]["account_id"] == account_id
    assert accts[0]["connection_state"] == "connected"

    blob = temp_db.load_calendar_credentials(user_id=auth_user_id, account_id=account_id)
    assert blob == b"encrypted_token_bytes_xyz"


def test_calendar_account_toggle_never_flashes_reconnect_needed(temp_db: LocalDatabase):
    engine = UnifiedCalendarEngine(db=temp_db, user_id="test_user")
    mock_prov = MockCalendarProvider()
    engine.register_provider("google:test@gmail.com", mock_prov)

    # Initially healthy or syncing
    health = engine.get_sync_health_summary()
    assert health["state"] in ("syncing", "healthy")
    assert health["healthy_accounts"] >= 0

    # Pause account
    ok = engine.set_account_enabled("google:test@gmail.com", False)
    assert ok is True
    health_paused = engine.get_sync_health_summary()
    assert health_paused["state"] == "paused"
    assert health_paused["paused_accounts"] == 1

    # Resume account: must immediately transition to 'syncing' or 'healthy', NEVER 'attention_needed'
    ok = engine.set_account_enabled("google:test@gmail.com", True)
    assert ok is True
    health_resumed = engine.get_sync_health_summary()
    assert health_resumed["state"] in ("syncing", "healthy")
    assert health_resumed["state"] != "attention_needed"

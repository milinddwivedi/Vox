"""
Unit & Integration Tests for Hinglish & Scrum Daily Call Transcription and Resilient Offline Sync.
"""

from unittest.mock import MagicMock
import pytest

from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import merge_domain_hotwords
from voxlocal.calendar.unified_engine import UnifiedCalendarEngine


def test_hinglish_scrum_prompt_integrity():
    # initial_prompt must stay empty to prevent silence hallucinations
    assert DictationProcessor.DEFAULT_HINGLISH_PROMPT == ""

    hinglish_hw = DictationProcessor.build_whisper_hotwords([], language="en+hi").lower()
    for term in ("wfx", "plm", "erp", "hemanth", "rumjhum", "sprint", "daily scrum"):
        assert term in hinglish_hw

    merged_hw = merge_domain_hotwords(hinglish_hw).lower()
    for term in ("standup", "ticket", "pr", "sprint", "wfx", "plm", "erp", "hemanth", "rumjhum", "daily scrum"):
        assert term in merged_hw


def test_scrum_deva_loanwords_restoration():
    # Ticket ID variations
    t1 = DictationProcessor.restore_loanwords("ये तिकीट आयडी चेक कर लो")
    assert "ticket ID" in t1

    t2 = DictationProcessor.restore_loanwords("टिकट आईडी क्लोज कर दी")
    assert "ticket ID" in t2
    assert "close" in t2

    # Reopen and replicate
    t3 = DictationProcessor.restore_loanwords("इस बग को रीओपन करके रेप्लिकेट करो")
    assert "reopen" in t3
    assert "replicate" in t3

    # Audible and standup
    t4 = DictationProcessor.restore_loanwords("एम आई ऑडिबल इन दिस स्टैंडअप")
    assert "audible" in t4
    assert "standup" in t4


def test_domain_entity_corrections():
    # American Top Guns -> American Tall
    res1 = DictationProcessor.process("What about American Top Guns?", cleanup_enabled=True)
    assert "American Tall" in res1

    # Tiket aaeedi -> ticket ID
    res2 = DictationProcessor.process("Tiket aaeedi update ho gaya.", cleanup_enabled=True)
    assert "ticket ID" in res2 or "Ticket ID" in res2


def test_offline_cached_account_snapshot_retains_authenticated_state():
    engine = UnifiedCalendarEngine()
    mock_auth_broker = MagicMock()
    mock_auth_broker.load_credentials.return_value = {"refresh_token": "valid_token"}
    engine._auth_broker = mock_auth_broker

    db_meta = {
        "account_id": "ms_graph:user@outlook.com",
        "provider_type": "ms_graph",
        "account_email": "user@outlook.com",
        "display_name": "User",
        "enabled": True,
        "connection_state": "connected",
        "last_sync_at": "2026-11-16T10:00:00Z",
        "last_error": "Could not reach Microsoft right now (network offline).",
        "error_code": "NETWORK_OFFLINE",
        "is_healthy": True,
    }

    acc_id = "ms_graph:user@outlook.com"
    engine._account_meta[acc_id] = db_meta

    snap = engine._build_account_snapshot(acc_id, prov=None)
    assert snap["is_authenticated"] is True
    assert snap["connection_state"] == "offline_cached"
    assert snap["is_healthy"] is True

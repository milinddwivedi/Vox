"""
Unit & Integration Tests for Hinglish & Scrum Daily Call Transcription and Resilient Offline Sync.
"""

from unittest.mock import MagicMock
import pytest

from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.calendar.unified_engine import UnifiedCalendarEngine


def test_hinglish_scrum_prompt_integrity():
    prompt = DictationProcessor.DEFAULT_HINGLISH_PROMPT
    assert "standup" in prompt.lower()
    assert "ticket id" in prompt.lower()
    assert "pr" in prompt.lower()
    assert "sprint" in prompt.lower()
    assert "baad mein" in prompt.lower()


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


def test_hinglish_acoustic_confusion_corrections():
    # Bad man -> baad mein
    res1 = DictationProcessor.process("We will discuss this bad man.", cleanup_enabled=True)
    assert "baad mein" in res1.lower()
    assert "bad man" not in res1.lower()

    # Bat ye -> par yeh
    res2 = DictationProcessor.process("Bat ye nahi ho raha.", cleanup_enabled=True)
    assert "par yeh" in res2.lower()

    # You sabka -> yeh sabka
    res3 = DictationProcessor.process("You sabka update le lo.", cleanup_enabled=True)
    assert "yeh sabka" in res3.lower()

    # Peace peace -> please, please
    res4 = DictationProcessor.process("Peace peace hold on.", cleanup_enabled=True)
    assert "please, please" in res4.lower()

    # VeeTicket id / Tiket aaeedi -> ticket ID
    res5 = DictationProcessor.process("I shared this with veeticket id.", cleanup_enabled=True)
    assert "ticket ID" in res5

    # Help to join -> her to join
    res6 = DictationProcessor.process("Do you expect help to join?", cleanup_enabled=True)
    assert "her to join" in res6.lower()

    # American Top Guns -> American Tall
    res7 = DictationProcessor.process("What about American Top Guns?", cleanup_enabled=True)
    assert "American Tall" in res7


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

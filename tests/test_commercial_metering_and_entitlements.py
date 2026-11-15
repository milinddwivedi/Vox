"""
Comprehensive tests for VOX Commercial Metering, Cryptographic Entitlements,
Multilingual Word Counting, and Safe Downgrade Policies.
"""

import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from voxlocal.core.metering import (
    UsageMeteringEngine,
    count_committed_words,
    get_current_week_period_utc,
)
from voxlocal.core.entitlement import (
    EntitlementManager,
    EntitlementState,
    sign_entitlement_payload,
    verify_and_decode_entitlement_token,
)


class TestMultilingualWordCounting:
    """Tests for accurate word counting across English, Hindi, Hinglish, and symbols."""

    def test_pure_english(self):
        text = "Hello world from the VOX local assistant"
        assert count_committed_words(text) == 7

    def test_hindi_devanagari(self):
        text = "नमस्ते दुनिया यह एक परीक्षण है"
        assert count_committed_words(text) == 6

    def test_hinglish(self):
        text = "Kal meeting me discuss karenge presentation details"
        assert count_committed_words(text) == 7

    def test_mixed_numbers_and_words(self):
        text = "Model 3.2 has 1B parameters and costs 199 INR"
        count = count_committed_words(text)
        assert count in (8, 9, 10)

    def test_pure_punctuation_and_whitespace(self):
        text = "   ... !!! ,,, ??? --- \n\t   "
        assert count_committed_words(text) == 0

    def test_empty_string(self):
        assert count_committed_words("") == 0
        assert count_committed_words(None) == 0


class TestWeeklyPeriodCalculation:
    """Tests for Monday 00:00 UTC cycle calculations."""

    def test_current_week_format(self):
        start_dt, end_dt, period = get_current_week_period_utc()
        assert period.startswith("utc_week_")
        assert start_dt.weekday() == 0  # Monday
        assert end_dt > start_dt


class TestMeteringEngine:
    """Tests for local SQLite usage metering with cryptographic seal."""

    @pytest.fixture
    def temp_metering(self):
        temp_dir = tempfile.mkdtemp()
        db_path = Path(temp_dir) / "test_vox.db"
        engine = UsageMeteringEngine(db_path)
        yield engine
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_initial_usage_is_zero(self, temp_metering):
        usage = temp_metering.get_weekly_usage()
        assert usage["dictation_words"] == 0
        assert usage["meeting_duration_sec"] == 0.0
        assert usage["meeting_count"] == 0
        assert usage["ask_vox_count"] == 0

    def test_record_committed_dictation(self, temp_metering):
        temp_metering.record_committed_dictation("One two three four five")
        usage = temp_metering.get_weekly_usage()
        assert usage["dictation_words"] == 5

        temp_metering.record_committed_dictation("Six seven eight nine ten")
        usage = temp_metering.get_weekly_usage()
        assert usage["dictation_words"] == 10

    def test_dictation_quota_enforcement(self, temp_metering):
        ent_mgr = EntitlementManager()
        # Free tier has 3,000 words limit
        can_start, reason = temp_metering.can_start_dictation(ent_mgr)
        assert can_start is True

        # Simulate recording 3,000 words
        big_text = "word " * 3000
        temp_metering.record_committed_dictation(big_text)

        can_start, reason = temp_metering.can_start_dictation(ent_mgr)
        assert can_start is False
        assert "3,000 words" in reason

    def test_80_percent_early_warning_threshold(self, temp_metering):
        ent_mgr = EntitlementManager()
        # 80% of 3000 is 2400 words
        text_2400 = "word " * 2400
        temp_metering.record_committed_dictation(text_2400)
        warnings = temp_metering.check_early_warnings(ent_mgr)
        assert any("dictation_words" in w.get("metric", "") for w in warnings)

    def test_meeting_overrun_non_terminating_policy(self, temp_metering):
        ent_mgr = EntitlementManager()
        # Free cap is 5400s (90m). Active meeting at 5500s should warn but NOT terminate
        status = temp_metering.evaluate_live_meeting_overrun(5500, ent_mgr)
        assert status["warning"] is True
        assert status["overrun"] is True
        assert "90-minute" in status["message"]

    def test_record_completed_meeting(self, temp_metering):
        temp_metering.record_completed_meeting(120.5, meeting_id=1)
        usage = temp_metering.get_weekly_usage()
        assert usage["meeting_count"] == 1
        assert usage["meeting_duration_sec"] == 120.5

    def test_ask_vox_query_metering(self, temp_metering):
        ent_mgr = EntitlementManager()
        for i in range(10):
            can_ask, _ = temp_metering.can_ask_vox(ent_mgr)
            assert can_ask is True
            temp_metering.record_ask_vox_query(f"Question {i}")

        # 11th query on Free tier should be blocked
        can_ask, reason = temp_metering.can_ask_vox(ent_mgr)
        assert can_ask is False
        assert "10 queries" in reason


class TestEntitlementsAndOfflineGrace:
    """Tests for signed entitlement tokens, DPAPI vault, and 72-hour grace period."""

    def test_sign_and_verify_valid_token(self):
        payload = {
            "user_id": "usr_test_123",
            "plan_code": "pro",
            "is_pro": True,
            "exp": int(time.time()) + 3600,
        }
        token = sign_entitlement_payload(payload, secret_key="test_secret_key_12345")
        valid, decoded, err = verify_and_decode_entitlement_token(token, verify_key="test_secret_key_12345")
        assert valid is True
        assert decoded is not None
        assert decoded["user_id"] == "usr_test_123"
        assert decoded["is_pro"] is True

    def test_tampered_token_rejected(self):
        payload = {"user_id": "usr_test_123", "plan_code": "pro", "is_pro": True}
        token = sign_entitlement_payload(payload, secret_key="test_secret_key_12345")
        tampered_token = token[:-4] + "fake"
        valid, decoded, err = verify_and_decode_entitlement_token(tampered_token, verify_key="test_secret_key_12345")
        assert valid is False
        assert decoded is None

    def test_wrong_secret_rejected(self):
        payload = {"user_id": "usr_test_123", "plan_code": "pro", "is_pro": True}
        token = sign_entitlement_payload(payload, secret_key="test_secret_key_12345")
        valid, decoded, err = verify_and_decode_entitlement_token(token, verify_key="wrong_secret_key")
        assert valid is False
        assert decoded is None

    def test_entitlement_manager_offline_grace(self):
        temp_dir = tempfile.mkdtemp()
        vault_path = Path(temp_dir) / "test_entitlement.vault"
        mgr = EntitlementManager(cache_file=vault_path)

        # By default, starts in Free tier
        ent = mgr.get_effective_entitlement()
        assert ent["is_pro"] is False
        assert ent["plan"] == "free"

        # Simulate offline grace
        mgr._current_state = EntitlementState(
            user_id="usr_offline",
            plan_code="pro",
            status="offline_grace",
            is_pro=True,
            offline_hours_remaining=48.0,
        )
        ent_grace = mgr.get_effective_entitlement()
        assert ent_grace["is_pro"] is True
        assert ent_grace["in_offline_grace"] is True
        assert ent_grace["offline_hours_remaining"] == 48.0

        # When expired, safe fallback
        mgr.clear_entitlement()
        ent_expired = mgr.get_effective_entitlement()
        assert ent_expired["is_pro"] is False
        assert ent_expired["plan"] == "free"

        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_downgrade_preserves_local_data(self):
        temp_dir = tempfile.mkdtemp()
        db_path = Path(temp_dir) / "test_vox.db"
        engine = UsageMeteringEngine(db_path)

        # Record meetings and dictation during Pro
        engine.record_committed_dictation("Some confidential corporate meeting transcript notes")
        engine.record_completed_meeting(3600.0, meeting_id=101)

        # Downgrade to Free
        vault_path = Path(temp_dir) / "test_entitlement.vault"
        mgr = EntitlementManager(cache_file=vault_path)
        mgr.revert_to_free()

        # Verify data is 100% intact
        usage = engine.get_weekly_usage()
        assert usage["dictation_words"] == 6
        assert usage["meeting_duration_sec"] == 3600.0
        assert usage["meeting_count"] == 1
        assert mgr.get_effective_entitlement()["is_pro"] is False

        shutil.rmtree(temp_dir, ignore_errors=True)

"""
End-to-End Live Supabase Cloud Database & Customer Journey Verification.
Target Database: Supabase Project qiigoipgoedexmdowtvj (https://qiigoipgoedexmdowtvj.supabase.co)

Validates:
1. Live REST API connectivity and HTTPS status 200
2. Default commercial plans (VOX Free and VOX Pro) seeded in database
3. Weekly quota limits structure and feature matrix integrity
4. Ed25519 token minting and signature verification for authenticated Pro customers
5. Multi-metric usage aggregation and weekly quota enforcement logic
"""

import json
import time
import urllib.request
import pytest
from voxlocal.core.entitlement import (
    EntitlementManager,
    EntitlementState,
    verify_and_decode_entitlement_token,
    DEFAULT_ENTITLEMENT_PUBLIC_KEY,
)
from tests.token_test_helper import sign_entitlement_payload
from voxlocal.core.metering import UsageMeteringEngine


SUPABASE_URL = "https://qiigoipgoedexmdowtvj.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFpaWdvaXBnb2VkZXhtZG93dHZqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ3NTM2NjQsImV4cCI6MjExMDMyOTY2NH0."
    "C9Bz6OYqxB5uBUa0kEBJpiMvwW6SbKYWhAEcVZoFvYM"
)

TEST_PRIV_KEY = "eCqPit3zEc3qLCPuzKLqf/QTf1gyHqHGVR6v4qDoLEE="
TEST_PUB_KEY = "YsgKdWBVb1ZfhvSxZeietMw9BZokUSRcR8WgH7GoMLI="


class TestLiveSupabasePlans:
    """Validates public plans endpoint against live Supabase cloud database."""

    def test_live_supabase_plans_endpoint_accessible(self):
        url = f"{SUPABASE_URL}/rest/v1/plans?select=*"
        req = urllib.request.Request(
            url,
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert len(data) >= 2
            plan_ids = {p["id"] for p in data}
            assert "free" in plan_ids
            assert "pro" in plan_ids

    def test_free_plan_commercial_quotas(self):
        url = f"{SUPABASE_URL}/rest/v1/plans?id=eq.free&select=*"
        req = urllib.request.Request(
            url,
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert len(data) == 1
            free_plan = data[0]
            assert free_plan["name"] == "VOX Free"
            assert free_plan["price_inr_monthly"] == 0
            assert free_plan["price_inr_annual"] == 0
            limits = free_plan["limits"]
            assert limits["dictation_words_per_week"] == 3000
            assert limits["meeting_duration_sec_per_week"] == 10800
            assert limits["meeting_count_per_week"] == 5
            assert limits["max_meeting_duration_sec"] == 5400
            assert limits["ask_vox_queries_per_week"] == 10
            assert limits["allow_meeting_overrun"] is True

    def test_pro_plan_commercial_pricing_and_unlimited_limits(self):
        url = f"{SUPABASE_URL}/rest/v1/plans?id=eq.pro&select=*"
        req = urllib.request.Request(
            url,
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert len(data) == 1
            pro_plan = data[0]
            assert pro_plan["name"] == "VOX Pro"
            assert pro_plan["price_inr_monthly"] == 199
            assert pro_plan["price_inr_annual"] == 1599
            limits = pro_plan["limits"]
            assert limits["dictation_words_per_week"] is None
            assert limits["meeting_duration_sec_per_week"] is None
            assert limits["meeting_count_per_week"] is None
            assert limits["ask_vox_queries_per_week"] is None
            features = pro_plan["features"]
            assert features["unlimited_words"] is True
            assert features["unlimited_meetings"] is True
            assert features["unlimited_ask_vox"] is True


class TestLiveCustomerJourneyEntitlementsAndUsage:
    """Validates entitlement and quota enforcement for Free and Pro customers."""

    def test_customer_registration_free_tier_limits(self, tmp_path):
        db_path = str(tmp_path / "test_voxlocal.db")
        meter = UsageMeteringEngine(db_path=db_path)
        meter.set_user_context("user_reg_001")
        ent_mgr = EntitlementManager()

        # Initial state has full 3000 words available
        can_start, reason = meter.can_start_dictation(ent_mgr)
        assert can_start is True

        # Commit 2500 words
        meter.record_committed_dictation("word " * 2500)
        usage = meter.get_weekly_usage()
        assert usage["dictation_words"] == 2500

        # Check early warnings (2500 > 2400 words = 80%)
        warnings = meter.check_early_warnings(ent_mgr)
        assert any("dictation_words" in w.get("metric", "") for w in warnings)

        # Commit another 600 words -> total 3100
        meter.record_committed_dictation("word " * 600)
        can_start, reason = meter.can_start_dictation(ent_mgr)
        assert can_start is False
        assert "3,000 words" in reason

    def test_pro_customer_unlimited_entitlement(self, tmp_path):
        vault_path = tmp_path / "test_entitlement.vault"
        mgr = EntitlementManager(cache_file=vault_path, verify_key=TEST_PUB_KEY)
        mgr.set_user_context("user_pro_001")

        payload = {
            "sub": "user_pro_001",
            "user_id": "user_pro_001",
            "is_pro": True,
            "plan_code": "pro",
            "token_version": 1,
            "iat": int(time.time()),
            "exp": int(time.time()) + 86400 * 7,
            "limits": {
                "dictation_words_per_week": None,
                "meeting_duration_sec_per_week": None,
                "meeting_count_per_week": None,
                "ask_vox_queries_per_week": None,
                "allow_meeting_overrun": True,
            },
        }

        token = sign_entitlement_payload(payload, private_key=TEST_PRIV_KEY)
        ok, reason = mgr.save_entitlement_token(token, expected_user_id="user_pro_001")
        assert ok is True
        assert mgr.is_pro() is True
        assert mgr.is_offline_expired() is False
        assert mgr.is_feature_enabled("unlimited_words") is True
        assert mgr.is_feature_enabled("unlimited_meetings") is True

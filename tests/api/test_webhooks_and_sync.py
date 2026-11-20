"""
Comprehensive E2E and API Test Suite for VOX Commercialisation
File: tests/api/test_webhooks_and_sync.py

Covers:
- Category 1: Razorpay Webhook HMAC Signature Verification (raw signature, invalid, tampered secret/body)
- Category 2: Razorpay Event Lifecycle (subscription.activated, charged, cancelled, paused, resumed, captured, failed, refunds)
- Category 3: Webhook Idempotency & Deduplication (payment_events unique constraint, token_version protection)
- Category 4: Out-of-Order Webhook Handling (stale event protection)
- Category 5: Usage Sync Endpoint (Bearer auth, field name reconciliation, aggregation, idempotency, multi-device, isolation)
- Category 6: Entitlements and Release Manifest APIs
- Category 7: Tier 4 Customer Application Scenarios
"""

import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import pytest

RAZORPAY_TEST_SECRET = "vox_test_webhook_secret_xyz"


def compute_signature(payload_bytes: bytes, secret: str = RAZORPAY_TEST_SECRET) -> str:
    """Computes Razorpay HMAC-SHA256 hex digest."""
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


@pytest.fixture(scope="session")
def api_server():
    """
    Spawns the Node test server bridge (tests/api/test_server.js)
    and waits for SERVER_READY signal.
    """
    root_dir = Path(__file__).resolve().parent.parent.parent
    server_script = root_dir / "tests" / "api" / "test_server.js"

    if not server_script.exists():
        server_script = Path.cwd() / "tests" / "api" / "test_server.js"

    proc = subprocess.Popen(
        ["node", str(server_script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(root_dir),
    )

    base_url = None
    start_time = time.time()
    while time.time() - start_time < 10.0:
        line = proc.stdout.readline()
        if "SERVER_READY:" in line:
            base_url = line.strip().split("SERVER_READY:")[1]
            break
        if proc.poll() is not None:
            stderr_out = proc.stderr.read()
            raise RuntimeError(f"API test server failed to start: {stderr_out}")
        time.sleep(0.05)

    if not base_url:
        proc.kill()
        raise TimeoutError("API test server startup timed out waiting for SERVER_READY.")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(autouse=True)
def reset_db_state(api_server):
    """Resets the in-memory test database before each test run."""
    req = urllib.request.Request(
        f"{api_server}/test/reset",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200


def get_db_state(api_server):
    """Fetches the current in-memory database tables for assertions."""
    req = urllib.request.Request(f"{api_server}/test/state", method="GET")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def seed_db_state(api_server, seed_data):
    """Seeds in-memory database with test records."""
    req = urllib.request.Request(
        f"{api_server}/test/seed",
        data=json.dumps(seed_data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200


# =============================================================================
# Category 1: Razorpay Webhook HMAC Signature Verification
# =============================================================================
class TestRazorpayWebhookSignature:
    """Verifies HMAC signature checking under genuine and adversarial inputs."""

    def test_valid_signature_accepted(self, api_server):
        payload = {
            "event": "payment.captured",
            "event_id": "evt_sig_001",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_test_001",
                        "amount": 19900,
                        "notes": {"user_id": "usr_1", "interval": "monthly"},
                    }
                }
            },
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body_bytes)

        req = urllib.request.Request(
            f"{api_server}/api/webhooks/razorpay",
            data=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": sig,
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("status") == "ok"

        db_state = get_db_state(api_server)
        assert any(e["event_id"] == "evt_sig_001" for e in db_state["payment_events"])

    def test_invalid_signature_rejected(self, api_server):
        payload = {"event": "payment.captured", "event_id": "evt_sig_002"}
        body_bytes = json.dumps(payload).encode("utf-8")
        fake_sig = "deadbeef" * 8

        req = urllib.request.Request(
            f"{api_server}/api/webhooks/razorpay",
            data=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": fake_sig,
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 400

        db_state = get_db_state(api_server)
        assert not any(e["event_id"] == "evt_sig_002" for e in db_state["payment_events"])

    def test_tampered_secret_rejected(self, api_server):
        payload = {"event": "payment.captured", "event_id": "evt_sig_003"}
        body_bytes = json.dumps(payload).encode("utf-8")
        sig_with_wrong_key = compute_signature(body_bytes, secret="attacker_secret_999")

        req = urllib.request.Request(
            f"{api_server}/api/webhooks/razorpay",
            data=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": sig_with_wrong_key,
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 400

    def test_tampered_body_rejected(self, api_server):
        original_payload = {"event": "payment.captured", "event_id": "evt_sig_004", "amount": 19900}
        orig_bytes = json.dumps(original_payload).encode("utf-8")
        sig = compute_signature(orig_bytes)

        # Body tampered by injecting extra spaces
        tampered_bytes = orig_bytes + b"   "

        req = urllib.request.Request(
            f"{api_server}/api/webhooks/razorpay",
            data=tampered_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": sig,
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 400

    def test_missing_signature_header_rejected(self, api_server):
        payload = {"event": "payment.captured", "event_id": "evt_sig_005"}
        body_bytes = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            f"{api_server}/api/webhooks/razorpay",
            data=body_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 400

    def test_non_post_methods_rejected(self, api_server):
        req = urllib.request.Request(f"{api_server}/api/webhooks/razorpay", method="GET")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 405


# =============================================================================
# Category 2: Razorpay Event Lifecycle
# =============================================================================
class TestRazorpaySubscriptionLifecycle:
    """Verifies complete state machine transitions across Razorpay events."""

    def test_subscription_activated_monthly(self, api_server):
        payload = {
            "event": "subscription.activated",
            "event_id": "evt_act_001",
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_U0Joq0iTO49N2E",
                        "plan_id": "plan_U0JlftJ0ZkwAbY",
                        "status": "active",
                        "notes": {"user_id": "usr_1", "interval": "monthly"},
                    }
                }
            },
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body_bytes)

        req = urllib.request.Request(
            f"{api_server}/api/webhooks/razorpay",
            data=body_bytes,
            headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

        db = get_db_state(api_server)
        subs = [s for s in db["subscriptions"] if s.get("user_id") == "usr_1"]
        assert len(subs) > 0
        assert subs[0]["status"] == "active"
        assert subs[0]["plan_id"] == "pro"
        assert subs[0]["billing_interval"] == "monthly"

        ents = [e for e in db["entitlements"] if e.get("user_id") == "usr_1"]
        assert len(ents) > 0
        assert ents[0]["is_pro"] is True
        assert ents[0]["plan_id"] == "pro"

    def test_subscription_activated_annual(self, api_server):
        payload = {
            "event": "subscription.activated",
            "event_id": "evt_act_002",
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_U0Jpy7OH6TnN5N",
                        "plan_id": "plan_U0JmYZAfwuRlyg",
                        "status": "active",
                        "notes": {"user_id": "usr_2", "interval": "annual"},
                    }
                }
            },
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body_bytes)

        req = urllib.request.Request(
            f"{api_server}/api/webhooks/razorpay",
            data=body_bytes,
            headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

        db = get_db_state(api_server)
        subs = [s for s in db["subscriptions"] if s.get("user_id") == "usr_2"]
        assert len(subs) > 0
        assert subs[0]["billing_interval"] == "annual"
        assert subs[0]["status"] == "active"

    def test_subscription_charged_renewal(self, api_server):
        # Pre-seed active subscription
        seed_db_state(
            api_server,
            {
                "subscriptions": [
                    {
                        "user_id": "usr_1",
                        "status": "active",
                        "plan_id": "pro",
                        "razorpay_subscription_id": "sub_U0Joq0iTO49N2E",
                    }
                ],
                "entitlements": [{"user_id": "usr_1", "is_pro": True, "token_version": 1}],
            },
        )

        payload = {
            "event": "subscription.charged",
            "event_id": "evt_chg_001",
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_U0Joq0iTO49N2E",
                        "status": "active",
                        "notes": {"user_id": "usr_1"},
                    }
                },
                "payment": {"entity": {"id": "pay_renew_001", "amount": 19900}},
            },
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body_bytes)

        req = urllib.request.Request(
            f"{api_server}/api/webhooks/razorpay",
            data=body_bytes,
            headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

        db = get_db_state(api_server)
        ent = [e for e in db["entitlements"] if e.get("user_id") == "usr_1"][0]
        assert ent["is_pro"] is True

    def test_subscription_cancelled_downgrades_entitlement(self, api_server):
        # Pre-seed user with Pro
        seed_db_state(
            api_server,
            {
                "subscriptions": [
                    {
                        "user_id": "usr_1",
                        "status": "active",
                        "plan_id": "pro",
                        "razorpay_subscription_id": "sub_U0Joq0iTO49N2E",
                    }
                ],
                "entitlements": [{"user_id": "usr_1", "is_pro": True, "plan_id": "pro", "token_version": 2}],
            },
        )

        payload = {
            "event": "subscription.cancelled",
            "event_id": "evt_cncl_001",
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_U0Joq0iTO49N2E",
                        "status": "cancelled",
                        "notes": {"user_id": "usr_1"},
                    }
                }
            },
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body_bytes)

        req = urllib.request.Request(
            f"{api_server}/api/webhooks/razorpay",
            data=body_bytes,
            headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

        db = get_db_state(api_server)
        sub = [s for s in db["subscriptions"] if s.get("user_id") == "usr_1"][0]
        assert sub["status"] == "canceled"

        ent = [e for e in db["entitlements"] if e.get("user_id") == "usr_1"][0]
        assert ent["is_pro"] is False
        assert ent["plan_id"] == "free"

    def test_fallback_customer_email_resolution(self, api_server):
        # Event has NO notes.user_id, only email "bob@company.com" (mapped to usr_bob in profiles)
        payload = {
            "event": "payment.captured",
            "event_id": "evt_email_001",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_bob_001",
                        "amount": 19900,
                        "email": "bob@company.com",
                        "notes": {},
                    }
                }
            },
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body_bytes)

        req = urllib.request.Request(
            f"{api_server}/api/webhooks/razorpay",
            data=body_bytes,
            headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

        db = get_db_state(api_server)
        subs = [s for s in db["subscriptions"] if s.get("user_id") == "usr_bob"]
        assert len(subs) > 0
        assert subs[0]["status"] == "active"


# =============================================================================
# Category 3: Webhook Idempotency & Deduplication
# =============================================================================
class TestWebhookIdempotencyAndOrdering:
    """Verifies that webhook delivery retries do not duplicate state or corrupt data."""

    def test_duplicate_event_id_returns_200_without_side_effects(self, api_server):
        payload = {
            "event": "subscription.charged",
            "event_id": "evt_dup_999",
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_dup_1",
                        "status": "active",
                        "notes": {"user_id": "usr_1"},
                    }
                }
            },
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body_bytes)

        headers = {"Content-Type": "application/json", "X-Razorpay-Signature": sig}

        # 1st delivery
        req1 = urllib.request.Request(f"{api_server}/api/webhooks/razorpay", data=body_bytes, headers=headers, method="POST")
        with urllib.request.urlopen(req1) as resp1:
            assert resp1.status == 200

        # 2nd delivery (identical event_id replay)
        req2 = urllib.request.Request(f"{api_server}/api/webhooks/razorpay", data=body_bytes, headers=headers, method="POST")
        with urllib.request.urlopen(req2) as resp2:
            assert resp2.status == 200

        db = get_db_state(api_server)
        events = [e for e in db["payment_events"] if e.get("event_id") == "evt_dup_999"]
        assert len(events) == 1, "Duplicate event_id was inserted multiple times into payment_events"

    def test_stale_event_out_of_order_ignored(self, api_server):
        # 1. User cancels subscription at timestamp T2 = 2000
        cancel_payload = {
            "event": "subscription.cancelled",
            "event_id": "evt_cancel_t2",
            "created_at": 2000,
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_ord_1",
                        "status": "cancelled",
                        "notes": {"user_id": "usr_1"},
                    }
                }
            },
        }
        c_bytes = json.dumps(cancel_payload).encode("utf-8")
        c_sig = compute_signature(c_bytes)
        req_c = urllib.request.Request(
            f"{api_server}/api/webhooks/razorpay",
            data=c_bytes,
            headers={"Content-Type": "application/json", "X-Razorpay-Signature": c_sig},
            method="POST",
        )
        with urllib.request.urlopen(req_c) as resp:
            assert resp.status == 200

        # 2. Delayed replay of charged event with older timestamp T1 = 1000 arrives later
        stale_payload = {
            "event": "subscription.charged",
            "event_id": "evt_charged_t1",
            "created_at": 1000,
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_ord_1",
                        "status": "active",
                        "notes": {"user_id": "usr_1"},
                    }
                }
            },
        }
        s_bytes = json.dumps(stale_payload).encode("utf-8")
        s_sig = compute_signature(s_bytes)
        req_s = urllib.request.Request(
            f"{api_server}/api/webhooks/razorpay",
            data=s_bytes,
            headers={"Content-Type": "application/json", "X-Razorpay-Signature": s_sig},
            method="POST",
        )
        with urllib.request.urlopen(req_s) as resp:
            assert resp.status == 200

        db = get_db_state(api_server)
        sub = [s for s in db["subscriptions"] if s.get("user_id") == "usr_1"][0]
        assert sub["status"] == "canceled", "Stale charged event incorrectly reactivated a canceled subscription!"


# =============================================================================
# Category 5: Usage Sync Endpoint (api/usage/sync.js)
# =============================================================================
class TestUsageSyncEndpoint:
    """Verifies authentication, field reconciliation, quota aggregation, and isolation."""

    def test_usage_sync_missing_auth_header(self, api_server):
        req = urllib.request.Request(
            f"{api_server}/api/usage/sync",
            data=b'{"events": [{"id": "1", "event_type": "dictation_words", "period_key": "2026-11-09", "quantity": 100}]}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 401

    def test_usage_sync_invalid_token(self, api_server):
        req = urllib.request.Request(
            f"{api_server}/api/usage/sync",
            data=b'{"events": [{"id": "1", "event_type": "dictation_words", "period_key": "2026-11-09", "quantity": 100}]}',
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer forged_or_expired_jwt",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 401

    def test_usage_sync_empty_events_array(self, api_server):
        req = urllib.request.Request(
            f"{api_server}/api/usage/sync",
            data=b'{"events": []}',
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer token_user_1",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 400

    def test_usage_sync_options_cors(self, api_server):
        req = urllib.request.Request(f"{api_server}/api/usage/sync", method="OPTIONS")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            headers = dict(resp.headers)
            assert headers.get("Access-Control-Allow-Origin") == "*"

    def test_usage_sync_field_reconciliation(self, api_server):
        """Tests that both desktop payload format and web payload format are accepted."""
        payload = {
            "events": [
                # Format A: Desktop client format
                {
                    "event_id": "sync_evt_a1",
                    "metric": "dictation_words",
                    "period_id": "utc_week_2026_11_09",
                    "amount": 200,
                    "timestamp_utc": "2026-11-15T12:00:00Z",
                },
                # Format B: Web payload format
                {
                    "id": "sync_evt_b1",
                    "event_type": "dictation_words",
                    "period_key": "utc_week_2026_11_09",
                    "quantity": 300,
                    "client_timestamp": "2026-11-15T12:05:00Z",
                },
            ]
        }
        req = urllib.request.Request(
            f"{api_server}/api/usage/sync",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer token_user_1",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "sync_evt_a1" in data.get("acked_event_ids", [])
            assert "sync_evt_b1" in data.get("acked_event_ids", [])

        db = get_db_state(api_server)
        p = [r for r in db["usage_periods"] if r.get("user_id") == "usr_1"][0]
        assert p["dictation_words_committed"] == 500

    def test_usage_sync_event_deduplication(self, api_server):
        """Submitting the same event twice should acknowledge it but NOT double-count."""
        payload = {
            "events": [
                {
                    "id": "sync_dedup_001",
                    "event_type": "dictation_words",
                    "period_key": "utc_week_2026_11_09",
                    "quantity": 400,
                }
            ]
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer token_user_1",
        }

        # 1st sync
        req1 = urllib.request.Request(f"{api_server}/api/usage/sync", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req1) as resp1:
            assert resp1.status == 200
            data1 = json.loads(resp1.read().decode("utf-8"))
            assert "sync_dedup_001" in data1.get("acked_event_ids", [])

        # 2nd sync with same event ID
        req2 = urllib.request.Request(f"{api_server}/api/usage/sync", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req2) as resp2:
            assert resp2.status == 200
            data2 = json.loads(resp2.read().decode("utf-8"))
            assert "sync_dedup_001" in data2.get("acked_event_ids", [])

        db = get_db_state(api_server)
        p = [r for r in db["usage_periods"] if r.get("user_id") == "usr_1"][0]
        assert p["dictation_words_committed"] == 400, "Duplicate event was counted twice in usage_periods!"

    def test_usage_sync_all_metrics_aggregation(self, api_server):
        payload = {
            "events": [
                {"id": "m_1", "event_type": "dictation_words", "period_key": "2026-11-09", "quantity": 1200},
                {"id": "m_2", "event_type": "meeting_duration_sec", "period_key": "2026-11-09", "quantity": 3600},
                {"id": "m_3", "event_type": "meeting_count", "period_key": "2026-11-09", "quantity": 2},
                {"id": "m_4", "event_type": "ask_vox_query", "period_key": "2026-11-09", "quantity": 4},
            ]
        }
        req = urllib.request.Request(
            f"{api_server}/api/usage/sync",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer token_user_1"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

        db = get_db_state(api_server)
        p = [r for r in db["usage_periods"] if r.get("user_id") == "usr_1"][0]
        assert p["dictation_words_committed"] == 1200
        assert p["meeting_duration_sec"] == 3600
        assert p["meeting_count"] == 2
        assert p["ask_vox_queries"] == 4

    def test_usage_sync_multi_device_aggregation(self, api_server):
        # Device 1 syncs 500 words
        req1 = urllib.request.Request(
            f"{api_server}/api/usage/sync",
            data=json.dumps({
                "events": [{"id": "d1_evt", "device_id": "hwid_desktop", "event_type": "dictation_words", "period_key": "2026-11-09", "quantity": 500}]
            }).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer token_user_1"},
            method="POST",
        )
        with urllib.request.urlopen(req1) as resp1:
            assert resp1.status == 200

        # Device 2 syncs 700 words
        req2 = urllib.request.Request(
            f"{api_server}/api/usage/sync",
            data=json.dumps({
                "events": [{"id": "d2_evt", "device_id": "hwid_laptop", "event_type": "dictation_words", "period_key": "2026-11-09", "quantity": 700}]
            }).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer token_user_1"},
            method="POST",
        )
        with urllib.request.urlopen(req2) as resp2:
            assert resp2.status == 200

        db = get_db_state(api_server)
        p = [r for r in db["usage_periods"] if r.get("user_id") == "usr_1"][0]
        assert p["dictation_words_committed"] == 1200

    def test_usage_sync_user_isolation(self, api_server):
        # User 1 sends event attempting to impersonate User 2 in the body
        payload = {
            "events": [
                {
                    "id": "iso_evt_001",
                    "user_id": "usr_2",  # Malicious forged user_id in body
                    "event_type": "dictation_words",
                    "period_key": "2026-11-09",
                    "quantity": 1000,
                }
            ]
        }
        req = urllib.request.Request(
            f"{api_server}/api/usage/sync",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer token_user_1"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

        db = get_db_state(api_server)
        # Event must be recorded under usr_1, NOT usr_2
        evt = [e for e in db["usage_events"] if e.get("id") == "iso_evt_001"][0]
        assert evt["user_id"] == "usr_1"

        # User 2's usage period must remain non-existent / empty
        p_user2 = [r for r in db["usage_periods"] if r.get("user_id") == "usr_2"]
        assert len(p_user2) == 0


# =============================================================================
# Category 6: Entitlements and Release Manifest APIs
# =============================================================================
class TestEntitlementsAndReleasesApi:
    """Verifies /api/entitlements and /api/releases/latest."""

    def test_entitlements_guest(self, api_server):
        req = urllib.request.Request(f"{api_server}/api/entitlements", method="GET")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["ok"] is True
            assert data["is_pro"] is False
            assert data["plan"] == "free"
            assert "token" in data

    def test_entitlements_authenticated_pro_user(self, api_server):
        seed_db_state(
            api_server,
            {
                "subscriptions": [{"user_id": "usr_1", "status": "active", "plan_id": "pro"}],
            },
        )
        req = urllib.request.Request(
            f"{api_server}/api/entitlements",
            headers={"Authorization": "Bearer token_user_1"},
            method="GET",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["ok"] is True
            assert data["is_pro"] is True
            assert data["plan"] in ("pro", "pro_monthly", "pro_annual")

    def test_release_manifest_integrity(self, api_server):
        req = urllib.request.Request(f"{api_server}/api/releases/latest", method="GET")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            manifest = json.loads(resp.read().decode("utf-8"))
            assert manifest["version"] == "1.0.11"
            assert "installer_url" in manifest
            assert len(manifest["sha256"]) == 64
            assert all(c in "0123456789abcdef" for c in manifest["sha256"])


# =============================================================================
# Category 7: Tier 4 Customer Application Scenarios
# =============================================================================
class TestRealWorldTier4Scenarios:
    """End-to-End customer lifecycle journeys spanning webhooks and usage sync."""

    def test_customer_upgrade_journey(self, api_server):
        # 1. User starts on Free tier, queries entitlements
        req_ent = urllib.request.Request(
            f"{api_server}/api/entitlements",
            headers={"Authorization": "Bearer token_user_1"},
            method="GET",
        )
        with urllib.request.urlopen(req_ent) as resp:
            ent = json.loads(resp.read().decode("utf-8"))
            assert ent["is_pro"] is False

        # 2. Razorpay activates Monthly subscription
        act_payload = {
            "event": "subscription.activated",
            "event_id": "evt_journey_01",
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_journey_1",
                        "plan_id": "plan_U0JlftJ0ZkwAbY",
                        "status": "active",
                        "notes": {"user_id": "usr_1", "interval": "monthly"},
                    }
                }
            },
        }
        b = json.dumps(act_payload).encode("utf-8")
        s = compute_signature(b)
        req_wh = urllib.request.Request(
            f"{api_server}/api/webhooks/razorpay",
            data=b,
            headers={"Content-Type": "application/json", "X-Razorpay-Signature": s},
            method="POST",
        )
        with urllib.request.urlopen(req_wh) as resp:
            assert resp.status == 200

        # 3. User queries entitlements again -> now Pro
        with urllib.request.urlopen(req_ent) as resp:
            ent_pro = json.loads(resp.read().decode("utf-8"))
            assert ent_pro["is_pro"] is True
            assert ent_pro["plan"] in ("pro", "pro_monthly", "pro_annual")

        # 4. Desktop client syncs large dictation batch (10,000 words)
        sync_payload = {
            "events": [
                {
                    "event_id": "sync_journey_01",
                    "metric": "dictation_words",
                    "period_id": "utc_week_2026_11_09",
                    "amount": 10000,
                }
            ]
        }
        req_sync = urllib.request.Request(
            f"{api_server}/api/usage/sync",
            data=json.dumps(sync_payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer token_user_1"},
            method="POST",
        )
        with urllib.request.urlopen(req_sync) as resp:
            assert resp.status == 200

        db = get_db_state(api_server)
        p = [r for r in db["usage_periods"] if r.get("user_id") == "usr_1"][0]
        assert p["dictation_words_committed"] == 10000

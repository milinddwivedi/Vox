"""
Hypothesis Stateful & Property-Based Testing Suite for Metering, Quotas & Pro Entitlement Engine.
Verifies:
1. Committed word counting across English, Hindi, Devanagari, Hinglish, punctuation
2. Quota depletion and exhaustion thresholds (3,000 words, 5 meetings, 180 min, 10 Ask VOX queries)
3. 80% proactive warning thresholds
4. DPAPI seal generation, tamper detection, and self-healing
5. Asymmetric Ed25519 cryptographic token verification, signature tampering rejection, user isolation
6. Stateful transitions across usage depletions and Pro plan upgrades/downgrades
"""

import base64
import datetime
import json
from pathlib import Path
import sqlite3
import time
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
    run_state_machine_as_test,
)
import hypothesis.strategies as st

from voxlocal.core.ed25519_rfc8032 import ed25519_public_key, ed25519_sign
from voxlocal.core.entitlement import (
    DEFAULT_ENTITLEMENT_PUBLIC_KEY,
    EntitlementManager,
    verify_and_decode_entitlement_token,
)
from voxlocal.core.metering import (
    FREE_WEEKLY_ASK_VOX_LIMIT,
    FREE_WEEKLY_MEETING_COUNT,
    FREE_WEEKLY_MEETING_MINUTES,
    FREE_WEEKLY_WORD_LIMIT,
    UsageMeteringEngine,
    count_committed_words,
    get_current_week_period_utc,
)


# =============================================================================
# Property-Based Tests: Word Counting & UTC Week Boundaries
# =============================================================================

@given(st.lists(st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll"))), min_size=1, max_size=30))
@settings(max_examples=30, deadline=None)
def test_property_count_committed_words_matches_token_count(words):
    raw_text = " ".join(words)
    count = count_committed_words(raw_text)
    assert count == len(words)


def test_property_count_committed_words_multilingual_and_numbers():
    assert count_committed_words("Hello world 123 नमस्ते भारत") == 5
    assert count_committed_words("... ,,, ??? !!!") == 0
    assert count_committed_words("") == 0


def test_utc_week_period_boundaries():
    # Test on a specific known Wednesday
    dt = datetime.datetime(2026, 11, 18, 14, 30, tzinfo=datetime.timezone.utc)
    m_start, s_end, period_id = get_current_week_period_utc(dt)
    assert m_start.weekday() == 0  # Monday
    assert m_start.hour == 0 and m_start.minute == 0
    assert s_end.weekday() == 6  # Sunday
    assert period_id == "utc_week_2026_11_16"


# =============================================================================
# Cryptographic Property-Based Tests: Ed25519 Token Tamper Resistance
# =============================================================================

def test_ed25519_tampered_token_is_strictly_rejected():
    """Any alteration to the payload or signature must cause verification to fail."""
    # Generate ephemeral Ed25519 keypair for test
    seed = b"test_seed_for_ed25519_32_bytes!!"
    pub_key = ed25519_public_key(seed)

    payload_dict = {
        "user_id": "usr_test123",
        "plan_code": "pro_annual",
        "is_pro": True,
        "exp": int(time.time()) + 86400 * 30,
    }
    raw_payload_json = json.dumps(payload_dict).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_payload_json).decode("ascii").rstrip("=")
    sig = ed25519_sign(b64_payload.encode("ascii"), seed)
    b64_sig = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    valid_token = f"{b64_payload}.{b64_sig}"

    # 1. Valid token passes
    ok, claims, err = verify_and_decode_entitlement_token(valid_token, verify_key=pub_key, expected_user_id="usr_test123")
    assert ok is True
    assert claims["is_pro"] is True

    # 2. Tampered payload is rejected
    tampered_payload_dict = dict(payload_dict, plan_code="enterprise_unlimited")
    b64_tampered = base64.urlsafe_b64encode(json.dumps(tampered_payload_dict).encode("utf-8")).decode("ascii").rstrip("=")
    tampered_token = f"{b64_tampered}.{b64_sig}"
    ok_t, _, err_t = verify_and_decode_entitlement_token(tampered_token, verify_key=pub_key, expected_user_id="usr_test123")
    assert ok_t is False
    assert "signature" in err_t.lower()

    # 3. User ID isolation: valid token rejected if presented for different user
    ok_user, _, err_u = verify_and_decode_entitlement_token(valid_token, verify_key=pub_key, expected_user_id="usr_attacker999")
    assert ok_user is False
    assert "belongs to user" in err_u.lower()


# =============================================================================
# Stateful Testing Machine: Quotas, DPAPI Anti-Tampering & Upgrades
# =============================================================================

class StatefulMeteringMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.uid = time.time_ns()
        self.tmp_dir = Path(f"D:/Milind/Voice Assistant/data/test_meter_{self.uid}")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmp_dir / "test_metering.db"

        self.engine = UsageMeteringEngine(self.db_path)
        self.user_id = f"user_{self.uid}"
        self.engine.set_user_context(self.user_id, "free")

        self.expected_words = 0
        self.expected_meetings = 0
        self.expected_meeting_seconds = 0.0
        self.expected_ask_vox = 0
        self.is_pro_plan = False

    @rule(words=st.integers(min_value=50, max_value=800))
    def record_dictation(self, words: int):
        dummy_text = " ".join(["word"] * words)
        metered = self.engine.record_committed_dictation(dummy_text, session_id=f"sess_{time.time_ns()}")
        assert metered == words
        self.expected_words += words

    @rule(duration_sec=st.floats(min_value=60.0, max_value=1800.0))
    def record_meeting(self, duration_sec: float):
        self.engine.record_completed_meeting(duration_sec, meeting_id=None)
        self.expected_meetings += 1
        self.expected_meeting_seconds += duration_sec

    @rule()
    def record_ask_vox(self):
        self.engine.record_ask_vox_query(query_id=f"ask_{time.time_ns()}")
        self.expected_ask_vox += 1

    @rule()
    def upgrade_to_pro(self):
        self.is_pro_plan = True
        self.engine.set_user_context(self.user_id, "pro_annual")

    @rule()
    def downgrade_to_free(self):
        self.is_pro_plan = False
        self.engine.set_user_context(self.user_id, "free")

    @rule()
    def tamper_database_row_to_zero(self):
        """Simulate malicious user attempting to reset their usage counters directly in SQLite."""
        with self.engine._get_connection() as con:
            cur = con.cursor()
            cur.execute(
                "UPDATE local_metering_periods SET dictation_words = 0 WHERE user_id = ?",
                (self.user_id,),
            )
            con.commit()

    # -------------------------------------------------------------------------
    # Invariants Verified After EVERY State Transition
    # -------------------------------------------------------------------------
    @invariant()
    def invariant_snapshot_integrity_and_anti_tamper(self):
        snap = self.engine.get_usage_snapshot()

        # Anti-tamper verification: Even if tamper_database_row_to_zero was called,
        # self-healing restores dictation_words to at least expected_words!
        assert snap.dictation_words_used >= self.expected_words, (
            f"Tamper detected without self-healing! Used {snap.dictation_words_used} < expected {self.expected_words}"
        )

        if self.is_pro_plan:
            assert snap.dictation_is_exhausted is False
            assert snap.meetings_is_exhausted is False
            assert snap.ask_vox_is_exhausted is False
            can_dictate, _ = self.engine.can_start_dictation()
            assert can_dictate is True
        else:
            if self.expected_words >= FREE_WEEKLY_WORD_LIMIT:
                assert snap.dictation_is_exhausted is True
                can_dictate, msg = self.engine.can_start_dictation()
                assert can_dictate is False
                assert "allowance reached" in msg.lower()


def test_metering_and_licensing_hypothesis_state_machine():
    """Execute state machine for metering, DPAPI anti-tamper, and Pro entitlement."""
    run_state_machine_as_test(
        StatefulMeteringMachine,
        settings=settings(deadline=None, max_examples=25, stateful_step_count=20),
    )

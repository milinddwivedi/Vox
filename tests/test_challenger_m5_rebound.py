"""
Empirical Challenger Test Suite: Window Rebound Elimination, Window Transitions,
Overlapping Meetings, and Unicode Clean Titles (Milestone 5).

Specifically verifies:
1. Window resize vs. new call on the same platform (same HWND vs different HWND).
2. Multiple overlapping meetings on different platforms (e.g., Teams + Google Meet + Zoom).
3. Window recreation and salted session ID generation under HWND recycling and Unicode titles.
4. Clean title sanitization with complex Unicode/Devanagari, brackets, and platform suffixes.
5. Window rebounding elimination and flicker tolerance.
"""

from __future__ import annotations

import collections
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.audio.call_detector import (
    CallDetector,
    MeetingDetectorState,
    MeetingSessionInfo,
    clean_meeting_title,
    is_leaving_title,
    is_non_call_hub,
    list_all_meeting_windows,
)


# =============================================================================
# 1. CLEAN TITLE SANITIZATION WITH COMPLEX UNICODE, BRACKETS & SUFFIXES
# =============================================================================

class TestEmpiricalTitleSanitization:
    """Empirical verification of clean_meeting_title with complex real-world edge cases."""

    def test_complex_unicode_devanagari_and_emojis(self) -> None:
        """Devanagari, emojis, and bracketed call tags are sanitized without loss of meeting title."""
        raw1 = "🚀 Architecture & Strategy / Dev Sync (नमस्ते) [Call] | Microsoft Teams"
        clean1 = clean_meeting_title(raw1, "Microsoft Teams")
        assert clean1 == "🚀 Architecture & Strategy / Dev Sync (नमस्ते)"

        raw2 = "[Teams Call] हिंदी बैठक [In Call] | Microsoft Teams"
        clean2 = clean_meeting_title(raw2, "Microsoft Teams")
        assert clean2 == "हिंदी बैठक"

        raw3 = "বাংলা মিটিং (Bangla Meeting) [Meeting] - Google Chrome"
        clean3 = clean_meeting_title(raw3, "Google Meet")
        assert clean3 == "বাংলা মিটিং (Bangla Meeting)"

        raw4 = "தமிழ் கலந்துரையாடல் (Tamil Discussion) [Huddle] | Microsoft Teams"
        clean4 = clean_meeting_title(raw4, "Microsoft Teams")
        assert clean4 == "தமிழ் கலந்துரையாடல் (Tamil Discussion)"

    def test_jira_ticket_brackets_preserved(self) -> None:
        """Square brackets containing ticket keys [PROJ-1234] are preserved; platform call tags stripped."""
        raw = "[PROJ-9821] Core Engine Refactoring [Call] | Microsoft Teams"
        clean = clean_meeting_title(raw, "Microsoft Teams")
        assert clean == "[PROJ-9821] Core Engine Refactoring"

        raw_sec = "[SEC-001] Emergency Incident Response [Meeting] - Microsoft Teams"
        clean_sec = clean_meeting_title(raw_sec, "Microsoft Teams")
        assert clean_sec == "[SEC-001] Emergency Incident Response"

        raw_teams_call = "[Teams Call] Unified Voice Pipeline [Call] | Microsoft Teams"
        clean_teams_call = clean_meeting_title(raw_teams_call, "Microsoft Teams")
        assert clean_teams_call == "Unified Voice Pipeline"

        raw_single_tag = "[Teams] Sprint Kickoff [Call] | Microsoft Teams"
        clean_single_tag = clean_meeting_title(raw_single_tag, "Microsoft Teams")
        assert clean_single_tag == "Sprint Kickoff"

    def test_google_meet_browser_title_matrix(self) -> None:
        """Google Meet browser tab titles with meeting codes and suffixes formatted cleanly."""
        assert clean_meeting_title("Meet - abc-defg-hij - Google Chrome", "Google Meet") == "Google Meet: abc-defg-hij"
        assert clean_meeting_title("Meet - abc-defg-hij - Microsoft Edge", "Google Meet") == "Google Meet: abc-defg-hij"
        assert clean_meeting_title("Meet - xyz-uvwx-rst", "Google Meet") == "Google Meet: xyz-uvwx-rst"
        assert clean_meeting_title("Google Meet", "Google Meet") == "Google Meet"


    def test_degenerate_and_empty_titles(self) -> None:
        """Empty, whitespace-only, or stripped-to-empty titles fall back to platform default."""
        assert clean_meeting_title("", "Microsoft Teams") == "Microsoft Teams Meeting"
        assert clean_meeting_title("   \t  \n ", "Zoom") == "Zoom Meeting"
        assert clean_meeting_title(None, "Google Meet") == "Google Meet Meeting"
        # Only annotations and platform suffix -> stripped to empty -> fallback
        assert clean_meeting_title("[Call] | Microsoft Teams", "Microsoft Teams") == "Microsoft Teams Meeting"
        assert clean_meeting_title("(Meeting) - Zoom", "Zoom") == "Zoom Meeting"
        assert clean_meeting_title("[In Call] - Google Meet", "Google Meet") == "Google Meet Meeting"


# =============================================================================
# 2. WINDOW RESIZE VS NEW CALL ON SAME PLATFORM
# =============================================================================

class TestEmpiricalResizeVsNewCall:
    """Empirical verification that resizing does not trigger rebound while new HWND triggers clean transition."""

    def test_one_hundred_rapid_window_resizes_preserve_session(self) -> None:
        """100 rapid window resizes on same HWND across multi-monitor bounds must never drop session."""
        detector = CallDetector()
        now = 100.0

        # Step 1: Detect initial Teams window
        win = [(2001, "Microsoft Teams", "Q4 Roadmap | Microsoft Teams", (0, 0, 1920, 1080))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win):
            detector.update(users=[], now=now)
            res = detector.update(users=[], now=now + 1.2)
            assert res == "started"

        assert detector.active_session is not None
        assert detector.active_session.window_hwnd == 2001
        sess_id = detector.active_session.session_id
        assert len(detector.prompted_session_ids) == 1

        # Step 2: 100 rapid resizes, moves across virtual screens, and size variations
        for i in range(100):
            # Vary x, y, width, height including multi-monitor negative coordinates
            x = -1920 + (i * 10)
            y = (i * 5) % 1080
            w = 800 + (i % 500)
            h = 600 + (i % 300)
            resized_win = [(2001, "Microsoft Teams", "Q4 Roadmap | Microsoft Teams", (x, y, w, h))]
            with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=resized_win):
                step_now = now + 2.0 + (i * 0.05)
                res_step = detector.update(users=[], now=step_now)
                assert res_step is None, f"Resize iteration {i} unexpectedly emitted: {res_step}"
                assert detector.active_session is not None
                assert detector.active_session.window_hwnd == 2001
                assert detector.active_session.session_id == sess_id

        # Session remains pristine, no suppression leaks
        assert len(detector.prompted_session_ids) == 1
        assert detector.state == MeetingDetectorState.AWAITING_USER_DECISION

    def test_new_call_same_platform_different_hwnd_transitions_cleanly(self) -> None:
        """Different HWND on same platform ends Meeting A in 1s grace period, then detects Meeting B in 1s."""
        detector = CallDetector()
        now = 500.0

        # Meeting A on HWND 3001
        win_a = [(3001, "Microsoft Teams", "Meeting A | Microsoft Teams", (0, 0, 1280, 720))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win_a):
            detector.update(users=[], now=now)
            assert detector.update(users=[], now=now + 1.2) == "started"

        sess_a_id = detector.active_session.session_id
        assert detector.active_session.window_hwnd == 3001

        # Meeting B opens on HWND 3002 (Meeting A closed)
        win_b = [(3002, "Microsoft Teams", "Meeting B | Microsoft Teams", (0, 0, 1280, 720))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win_b):
            # First tick flags explicit leave
            res1 = detector.update(users=[], now=now + 2.0)
            assert res1 is None
            # Advance past 1.0s explicit leave grace period
            res2 = detector.update(users=[], now=now + 3.1)
            assert res2 == "ended"
            assert detector.last_ended_session.session_id == sess_a_id
            assert detector.last_ended_session.window_hwnd == 3001
            assert detector.active_session is None

            # Next tick: Meeting B is discovered and starts debounce
            res3 = detector.update(users=[], now=now + 3.2)
            assert res3 is None
            # Advance past 1.0s debounce
            res4 = detector.update(users=[], now=now + 4.3)
            assert res4 == "started"
            assert detector.active_session.window_hwnd == 3002
            assert detector.active_session.session_id != sess_a_id
            assert detector.active_session.clean_title == "Meeting B"


# =============================================================================
# 3. WINDOW RECREATION AND SALTED SESSION ID GENERATION
# =============================================================================

class TestEmpiricalWindowRecreationAndSalting:
    """Empirical verification of session ID salting, HWND recycling, and suppression pruning."""

    def test_recycled_hwnd_distinct_timestamp_salts(self) -> None:
        """When OS reallocates the exact same HWND integer to a new window, session IDs must differ."""
        detector = CallDetector()
        now = 1000.0

        # Meeting 1: HWND 4001 at t=1000
        win1 = [(4001, "Microsoft Teams", "Sync 1 | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win1):
            with patch("time.time", return_value=1700001000):
                detector.update(users=[], now=now)
                assert detector.update(users=[], now=now + 1.2) == "started"

        sess1_id = detector.active_session.session_id
        assert "4001" in sess1_id
        assert "1700001000" in sess1_id

        # Meeting 1 ends immediately (e.g. user stops or call hangs up)
        detector.end_active_session_immediately()
        assert detector.active_session is None
        assert sess1_id not in detector.prompted_session_ids
        assert 4001 not in detector._hwnd_first_seen

        # Meeting 2 arrives 20 seconds later, OS reallocates identical HWND 4001
        win2 = [(4001, "Microsoft Teams", "Sync 1 | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win2):
            with patch("time.time", return_value=1700001020):
                detector.update(users=[], now=now + 20.0)
                res2 = detector.update(users=[], now=now + 21.5)
                assert res2 == "started"

        sess2_id = detector.active_session.session_id
        assert "4001" in sess2_id
        assert "1700001020" in sess2_id
        assert sess1_id != sess2_id, "Session IDs collided despite recycled HWND!"

    def test_unicode_only_titles_session_uniqueness(self) -> None:
        """Titles with non-ASCII Unicode (Devanagari/CJK) that yield empty regex slugs remain unique."""
        detector = CallDetector()
        now = 2000.0

        # Title 1: Hindi only
        win1 = [(5001, "Microsoft Teams", "नमस्ते | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win1):
            detector.update(users=[], now=now)
            assert detector.update(users=[], now=now + 1.2) == "started"

        id1 = detector.active_session.session_id
        detector.end_active_session_immediately()

        # Title 2: Japanese only
        win2 = [(5002, "Microsoft Teams", "会議 | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win2):
            detector.update(users=[], now=now + 5.0)
            assert detector.update(users=[], now=now + 6.2) == "started"

        id2 = detector.active_session.session_id
        assert id1 != id2
        assert "5001" in id1
        assert "5002" in id2


# =============================================================================
# 4. MULTIPLE OVERLAPPING MEETINGS ON DIFFERENT PLATFORMS
# =============================================================================

class TestEmpiricalOverlappingMeetings:
    """Empirical verification of concurrent meetings across platforms and stack counting."""

    def test_concurrent_teams_and_google_meet(self) -> None:
        """Simultaneous Teams window and Google Meet window do not clobber active session."""
        detector = CallDetector()
        now = 3000.0

        # Step 1: Teams meeting is active
        mock_wins_teams = [
            (6001, "Microsoft Teams", "Teams Executive Sync | Microsoft Teams", (0, 0, 1280, 720)),
        ]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_wins_teams):
            detector.update(users=[], now=now)
            assert detector.update(users=[], now=now + 1.2) == "started"

        assert detector.active_session.platform == "Microsoft Teams"
        assert detector.active_session.window_hwnd == 6001
        teams_sess_id = detector.active_session.session_id

        # Step 2: Google Meet call opens concurrently on HWND 6002
        mock_wins_both = [
            (6001, "Microsoft Teams", "Teams Executive Sync | Microsoft Teams", (0, 0, 1280, 720)),
            (6002, "Google Meet", "Meet - abc-defg-hij - Google Chrome", (1280, 0, 1280, 720)),
        ]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_wins_both):
            # Verify stack count reports 2
            assert detector.get_active_meeting_count() == 2

            # Poll update: Teams MUST remain active session without disruption
            res = detector.update(users=[], now=now + 5.0)
            assert res is None
            assert detector.active_session.platform == "Microsoft Teams"
            assert detector.active_session.session_id == teams_sess_id

        # Step 3: Teams meeting concludes, only Google Meet remains
        mock_wins_meet_only = [
            (6002, "Google Meet", "Meet - abc-defg-hij - Google Chrome", (1280, 0, 1280, 720)),
        ]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_wins_meet_only):
            # Teams ends after grace period (2.0s)
            detector.update(users=[], now=now + 10.0)
            res_end = detector.update(users=[], now=now + 12.5)
            assert res_end == "ended"
            assert detector.last_ended_session.platform == "Microsoft Teams"
            assert detector.active_session is None

            # Next tick: Google Meet is detected
            detector.update(users=[], now=now + 12.6)
            res_meet = detector.update(users=[], now=now + 14.0)
            assert res_meet == "started"
            assert detector.active_session.platform == "Google Meet"
            assert detector.active_session.window_hwnd == 6002
            assert "abc-defg-hij" in detector.active_session.clean_title

    def test_three_concurrent_platforms_stack_count(self) -> None:
        """Teams, Zoom, and Google Meet simultaneously open return stack count 3."""
        detector = CallDetector()
        mock_three = [
            (7001, "Microsoft Teams", "Teams Meeting", (0, 0, 800, 600)),
            (7002, "Zoom", "Zoom Webinar", (800, 0, 800, 600)),
            (7003, "Google Meet", "Meet - zzz-yyyy-xxx", (0, 600, 800, 600)),
        ]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_three):
            assert detector.get_active_meeting_count() == 3

        # One closes -> 2
        mock_two = [mock_three[0], mock_three[1]]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_two):
            assert detector.get_active_meeting_count() == 2

        # Two close -> 1
        mock_one = [mock_three[0]]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=mock_one):
            assert detector.get_active_meeting_count() == 1

        # All close -> 0
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=[]):
            assert detector.get_active_meeting_count() == 0


# =============================================================================
# 5. WINDOW REBOUNDING ELIMINATION & FLICKER TOLERANCE
# =============================================================================

class TestEmpiricalReboundElimination:
    """Empirical verification that transient window flickers do not end calls, while genuine departures do."""

    def test_transient_window_flicker_under_grace_period_does_not_drop_call(self) -> None:
        """Window vanishing for 0.8s (e.g. desktop switch / minimize) does not end call."""
        detector = CallDetector()
        now = 4000.0

        win = [(8001, "Microsoft Teams", "Sprint Retrospective | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win):
            detector.update(users=[], now=now)
            assert detector.update(users=[], now=now + 1.2) == "started"

        # Transient disappearance at now+2.0
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=[]):
            with patch("voxlocal.audio.call_detector.user32.IsWindow", return_value=False):
                res1 = detector.update(users=[], now=now + 2.0)
                assert res1 is None  # Grace period starts

        # Window reappears at now+2.8 (within 2.0s native window grace)
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win):
            res2 = detector.update(users=[], now=now + 2.8)
            assert res2 is None
            assert detector.active_session is not None
            assert detector.active_session.window_hwnd == 8001
            assert detector._released_at is None  # Grace timer reset

    def test_genuine_closure_past_grace_period_ends_call_and_prunes_suppression(self) -> None:
        """Window vanishing for >2.0s terminates call and prunes suppression for immediate next call."""
        detector = CallDetector()
        now = 5000.0

        win1 = [(9001, "Microsoft Teams", "Standup | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win1):
            detector.update(users=[], now=now)
            assert detector.update(users=[], now=now + 1.2) == "started"

        sess1_id = detector.active_session.session_id

        # Window closes permanently
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=[]):
            with patch("voxlocal.audio.call_detector.user32.IsWindow", return_value=False):
                detector.update(users=[], now=now + 2.0)
                # Advance 2.2s past 2.0s grace period
                res_end = detector.update(users=[], now=now + 4.2)
                assert res_end == "ended"

        assert detector.active_session is None
        assert detector.state == MeetingDetectorState.MEETING_ENDED
        assert sess1_id not in detector.prompted_session_ids
        assert 9001 not in detector._hwnd_first_seen


# =============================================================================
# 6. HIGH-CHURN, STRESS & ADVERSARIAL EDGE CASES
# =============================================================================

class TestEmpiricalHighChurnAndAdversarialStress:
    """Stress testing bounds, zero-width characters, dynamic in-call title mutations, and stack churn."""

    def test_dynamic_in_call_title_mutation_same_hwnd_preserved(self) -> None:
        """In-call notifications like '(Recording has started)' on same HWND do not restart session."""
        detector = CallDetector()
        now = 6000.0

        # Step 1: Meeting starts
        win_init = [(10001, "Microsoft Teams", "Security Review | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win_init):
            detector.update(users=[], now=now)
            assert detector.update(users=[], now=now + 1.2) == "started"

        original_id = detector.active_session.session_id

        # Step 2: In-call title mutations occur (recording banner, live captioning, participant count)
        mutations = [
            "Security Review (Recording) | Microsoft Teams",
            "Security Review (Transcribing) | Microsoft Teams",
            "Security Review (5) | Microsoft Teams",
            "Security Review | Microsoft Teams",
        ]
        for idx, m_title in enumerate(mutations):
            win_mut = [(10001, "Microsoft Teams", m_title, (0, 0, 800, 600))]
            with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win_mut):
                res = detector.update(users=[], now=now + 2.0 + (idx * 0.5))
                assert res is None, f"Mutation '{m_title}' caused unexpected transition: {res}"
                assert detector.active_session.session_id == original_id
                assert detector.active_session.window_hwnd == 10001

    def test_fifty_concurrent_meeting_windows_stack_stress(self) -> None:
        """50 concurrent open windows handle stack counting and ranking without lag or memory blowup."""
        detector = CallDetector()
        large_batch = []
        for i in range(50):
            plat = "Microsoft Teams" if i % 2 == 0 else "Zoom"
            large_batch.append((20000 + i, plat, f"Call #{i}", (0, 0, 800, 600)))

        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=large_batch):
            t0 = time.perf_counter()
            count = detector.get_active_meeting_count()
            duration = time.perf_counter() - t0
            assert count == 50
            assert duration < 0.05, f"Stack counting 50 windows took too long: {duration:.4f}s"

    def test_unicode_zero_width_and_rtl_titles(self) -> None:
        """Titles with zero-width spaces (\u200b), non-breaking spaces (\u00a0), and Arabic RTL are cleaned cleanly."""
        raw_rtl = "\u200fاجتماع الفريق التقني (Arabic Tech Sync) [Call] | Microsoft Teams"
        clean_rtl = clean_meeting_title(raw_rtl, "Microsoft Teams")
        assert "اجتماع الفريق التقني" in clean_rtl
        assert "(Arabic Tech Sync)" in clean_rtl
        assert "[Call]" not in clean_rtl
        assert "| Microsoft Teams" not in clean_rtl

        raw_zw = "Dev\u200bSync\u00a0Meeting [Call] | Microsoft Teams"
        clean_zw = clean_meeting_title(raw_zw, "Microsoft Teams")
        assert "Dev\u200bSync" in clean_zw
        assert "[Call]" not in clean_zw

    def test_back_to_back_zero_delay_meeting_swap(self) -> None:
        """Instantaneous meeting swap (0.0s gap) from HWND 30001 to HWND 30002 without idle gap."""
        detector = CallDetector()
        now = 7000.0

        # Start Meeting 1
        win1 = [(30001, "Microsoft Teams", "Session 1 | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win1):
            detector.update(users=[], now=now)
            assert detector.update(users=[], now=now + 1.2) == "started"

        s1_id = detector.active_session.session_id

        # Direct swap: Window 30001 replaced by Window 30002 at exact same tick
        win2 = [(30002, "Microsoft Teams", "Session 2 | Microsoft Teams", (0, 0, 800, 600))]
        with patch("voxlocal.audio.call_detector.list_all_meeting_windows", return_value=win2):
            # Tick 1: detects explicit leave because HWND changed
            res1 = detector.update(users=[], now=now + 2.0)
            assert res1 is None
            # Tick 2: 1.0s explicit leave grace period expires -> returns ended
            res2 = detector.update(users=[], now=now + 3.1)
            assert res2 == "ended"
            assert detector.last_ended_session.session_id == s1_id
            assert detector.active_session is None

            # Tick 3: discovers Session 2, sets up 1.0s debounce
            res3 = detector.update(users=[], now=now + 3.2)
            assert res3 is None
            # Tick 4: 1.0s debounce expires -> returns started for Session 2
            res4 = detector.update(users=[], now=now + 4.3)
            assert res4 == "started"
            assert detector.active_session.window_hwnd == 30002
            assert detector.active_session.session_id != s1_id
            assert detector.active_session.clean_title == "Session 2"

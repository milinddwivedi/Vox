"""Test Push-to-Talk Function Key, Live Dictation Interim Streaming, and Stability Fixes."""

import sys
from pathlib import Path

# Ensure src is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import pytest
import numpy as np
from unittest.mock import MagicMock

from voxlocal.win32.hotkeys import GlobalHotkeyManager, parse_hotkey_string
from voxlocal.calendar.unified_engine import UnifiedCalendarEngine, CalendarMeeting
from voxlocal.video.meeting_capture import user32, gdi32
from voxlocal.engine.transcriber import is_hallucination, LocalWhisperTranscriber


def test_hotkey_fn_alias_and_function_keys():
    """Verify 'fn' aliases to F8 and function keys parse correctly."""
    vks_fn = parse_hotkey_string("fn")
    assert 0x77 in vks_fn  # VK_F8

    vks_f8 = parse_hotkey_string("f8")
    assert 0x77 in vks_f8

    vks_f9 = parse_hotkey_string("F9")
    assert 0x78 in vks_f9

    vks_ctrl_win = parse_hotkey_string("ctrl+win")
    assert 0x11 in vks_ctrl_win
    assert 0x5B in vks_ctrl_win


def test_ptt_hold_triggers_in_toggle_mode():
    """Verify that toggle mode fires on_dictation_toggle, and push_to_talk/hybrid fire on_ptt_start."""
    mgr = GlobalHotkeyManager()
    ok, msg = mgr.configure(
        dictation_mode="toggle",
        dictation_hotkey="ctrl+shift+space",
        ptt_hotkey="f8",
        meeting_hotkey="ctrl+alt+r",
    )
    assert ok, msg

    toggles = []
    mgr.on_dictation_toggle = lambda: toggles.append(True)

    # In toggle mode, pressing F8 down triggers on_dictation_toggle
    mgr._handle_key_event(0x77, is_down=True, is_up=False)
    assert len(toggles) == 1

    # Releasing F8 resets the latch
    mgr._handle_key_event(0x77, is_down=False, is_up=True)
    assert mgr._dictation_latch is False

    # Now configure for push_to_talk mode
    mgr.configure(
        dictation_mode="push_to_talk",
        dictation_hotkey="ctrl+shift+space",
        ptt_hotkey="f8",
        meeting_hotkey="ctrl+alt+r",
    )
    ptt_started = []
    ptt_stopped = []
    mgr.on_ptt_start = lambda: ptt_started.append(True)
    mgr.on_ptt_stop = lambda: ptt_stopped.append(True)

    # Pressing F8 down triggers PTT start
    mgr._handle_key_event(0x77, is_down=True, is_up=False)
    assert len(ptt_started) == 1
    assert mgr._ptt_active is True

    # Releasing F8 triggers PTT stop
    mgr._handle_key_event(0x77, is_down=False, is_up=True)
    assert len(ptt_stopped) == 1
    assert mgr._ptt_active is False


def test_calendar_upcoming_meetings_limit_and_kwargs():
    """Verify get_upcoming_meetings accepts limit without raising TypeError."""
    from datetime import datetime, timezone, timedelta
    engine = UnifiedCalendarEngine(database=MagicMock())
    now = datetime.now(timezone.utc)
    m1 = CalendarMeeting(
        id="m1",
        title="Test 1",
        account_id="acc1",
        platform="teams",
        start_time=now + timedelta(hours=1),
        end_time=now + timedelta(hours=2),
        join_url="https://teams.microsoft.com",
    )
    m2 = CalendarMeeting(
        id="m2",
        title="Test 2",
        account_id="acc1",
        platform="meet",
        start_time=now + timedelta(hours=3),
        end_time=now + timedelta(hours=4),
        join_url="https://meet.google.com/abc-defg-hij",
    )
    with engine._lock:
        engine._cached_meetings = [m1, m2]

    # Calling with limit=1 (the crash trigger from floating_pill.py)
    res = engine.get_upcoming_meetings(limit=1)
    assert len(res) == 1
    assert res[0].title == "Test 1"

    # Calling with unexpected kwargs should not crash
    res_all = engine.get_upcoming_meetings(extra_arg=123)
    assert len(res_all) == 2


def test_meeting_capture_ctypes_signatures_64bit_safe():
    """Verify BitBlt, GetDC, PrintWindow ctypes signatures have explicit 64-bit argtypes."""
    assert gdi32.BitBlt.argtypes is not None
    assert len(gdi32.BitBlt.argtypes) == 9
    assert user32.GetDC.argtypes is not None
    assert len(user32.GetDC.argtypes) == 1
    assert user32.PrintWindow.argtypes is not None


def test_hallucination_detection_on_repeated_boilerplate():
    """Verify that hallucinated repeating phrases from Whisper silence are suppressed."""
    hallucinated = "So, I'm going to show you how to use the script. I'm going to show you how to use the script."
    assert is_hallucination(hallucinated) is True

    genuine_speech = "We need to fix the database synchronization and test the new calendar page."
    assert is_hallucination(genuine_speech) is False


def test_standalone_f_key_swallow_behavior():
    """Verify that standalone function keys (F8, F9) are swallowed during PTT while chords pass through."""
    mgr = GlobalHotkeyManager()
    mgr.configure(
        dictation_mode="toggle",
        dictation_hotkey="ctrl+shift+space",
        ptt_hotkey="f8",
        meeting_hotkey="ctrl+alt+r",
    )
    # F8 (0x77) is a standalone PTT key -> should be swallowed (returns True)
    swallow_f8 = mgr._handle_key_event(0x77, is_down=True, is_up=False)
    assert swallow_f8 is True

    # Ctrl (0x11) in a chord is not standalone -> should not be swallowed (returns False)
    swallow_ctrl = mgr._handle_key_event(0x11, is_down=True, is_up=False)
    assert swallow_ctrl is False


def test_base_model_caching_and_fast_resolution():
    """Verify that cached English dictation model resolves locally without network access."""
    transcriber = LocalWhisperTranscriber(model_name="small")
    path, dl_root, cached = transcriber.resolve_model_path("small")
    assert cached is True
    assert "models--Systran--faster-whisper-small" in path or Path(path).exists()


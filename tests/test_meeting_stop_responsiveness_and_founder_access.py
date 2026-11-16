"""
Tests for meeting teardown non-blocking responsiveness and Master Founder / Developer access.
"""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.core.entitlement import EntitlementManager, is_master_developer
from voxlocal.core.metering import UsageMeteringEngine


def test_is_master_developer_rules():
    assert is_master_developer("50d9a219-b06d-4fe9-acb3-7e32bf7cec2f", None) is True
    assert is_master_developer(None, "milinddwivedi0@gmail.com") is True
    assert is_master_developer("milinddwivedi0@gmail.com", None) is True
    assert is_master_developer("some-other-uuid", "MILINDDWIVEDI0@GMAIL.COM") is True
    assert is_master_developer("random-id", "someone@example.com") is False


def test_founder_entitlement_and_metering_unrestricted(tmp_path: Path):
    ent = EntitlementManager(cache_file=tmp_path / "ent.vault")
    meter = UsageMeteringEngine(db_path=tmp_path / "meter.db")

    # Set user context for founder
    ent.set_user_context("50d9a219-b06d-4fe9-acb3-7e32bf7cec2f", email="milinddwivedi0@gmail.com")
    meter.set_user_context("50d9a219-b06d-4fe9-acb3-7e32bf7cec2f", email="milinddwivedi0@gmail.com")

    assert ent.is_pro() is True
    assert meter.is_pro() is True

    eff = ent.get_effective_entitlement()
    assert eff["is_pro"] is True
    assert eff["plan"] == "pro_annual"
    assert eff["plan_name"] == "VOX Pro"
    assert eff["limits"]["dictation_words_weekly"] is None
    assert eff["limits"]["allow_meeting_overrun"] is True

    can_dict, _ = meter.can_start_dictation(ent)
    assert can_dict is True

    can_meet, _ = meter.can_start_meeting(ent)
    assert can_meet is True

    overrun = meter.evaluate_live_meeting_overrun(7200.0, ent)
    assert overrun["warning"] is False


def test_stop_meeting_recording_non_blocking_sla(tmp_path: Path):
    """
    Ensure stop_meeting_recording() returns in <150ms on the calling thread,
    even if the audio recorder, video recorder, or streaming transcriber take several seconds to stop.
    """
    import sys
    app = QApplication.instance() or QApplication(sys.argv)

    cfg = AppConfig()
    cfg.storage_dir = str(tmp_path / "data")
    cfg.ensure_directories()

    ctrl = AppController(cfg)
    ctrl._capture_state = "recording"
    ctrl._active_meeting_id = 12345

    # Simulate slow components that take 1.5 seconds each to join/teardown
    mock_streaming = MagicMock()
    def slow_streaming_stop():
        time.sleep(0.5)
    mock_streaming.stop = MagicMock(side_effect=slow_streaming_stop)
    ctrl.streaming_transcriber = mock_streaming

    mock_meeting_rec = MagicMock()
    mock_res = MagicMock()
    mock_res.duration_sec = 60.0
    mock_res.mixed_wav_path = str(tmp_path / "data" / "mixed.wav")
    def slow_rec_stop(*args, **kwargs):
        time.sleep(0.5)
        return mock_res
    mock_meeting_rec.stop = MagicMock(side_effect=slow_rec_stop)
    ctrl.meeting_recorder = mock_meeting_rec

    t0 = time.perf_counter()
    ctrl.stop_meeting_recording()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # Calling thread must return immediately without waiting for slow component stops
    assert ctrl._capture_state == "idle"
    assert elapsed_ms < 150.0, f"stop_meeting_recording blocked caller for {elapsed_ms:.1f}ms! SLA is <150ms."

    # The background worker handles the teardown
    if ctrl._finalize_thread:
        ctrl._finalize_thread.join(timeout=3.0)
    assert mock_streaming.stop.called
    assert mock_meeting_rec.stop.called

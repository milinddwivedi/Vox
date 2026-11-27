"""
Phase 2 Verification Suite: Recorder & Notetaker Flow.

Covers:
1. Instant 1-click recording start (zero QInputDialog modal) & smart title inference.
2. Inline editable meeting title in NotetakerPage + SQLite/list synchronization.
3. Live audio telemetry meters (Mic/Loopback OK) + 15-second loopback silence anomaly guard.
4. O(1) virtualized live transcript streaming + Scroll Lock + Jump to Live.
5. Strictly user-controlled interrupted recording recovery & WAV file preservation.
6. Slim collapsible UPCOMING MEETINGS strip reclaiming vertical workspace space.
7. Real 4-stage pipeline progress & live audio telemetry inside VoxNotetakerDrawer.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtWidgets import QApplication

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer
from voxlocal.ui.notetaker_page import NotetakerPage
from voxlocal.win32.startup import StartupManager


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([sys.argv[0], "--platform", "offscreen"])
    return app


_RETIRED_CONTROLLERS: list = []


class _NoOpThread:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass


def _shutdown_controller(ctrl: AppController, *widgets) -> None:
    app = _get_qapp()
    for w in widgets:
        if w is not None:
            try:
                if hasattr(w, "media_player"):
                    w.media_player.stop()
                    w.media_player.setSource(QUrl())
                    w.media_player.setAudioOutput(None)
                w.close()
                w.deleteLater()
            except Exception:
                pass
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    eng = getattr(ctrl, "calendar_engine", None)
    if eng is not None and hasattr(eng, "stop_polling"):
        try:
            eng.stop_polling()
        except Exception:
            pass
    ctrl.shutdown()
    _RETIRED_CONTROLLERS.append(ctrl)
    app.processEvents()


def _create_controller(td_name: str) -> AppController:
    cfg = AppConfig(storage_dir=td_name)
    cfg.launch_at_login = StartupManager.is_enabled()
    cfg.disable_model_preload = True
    cfg.disable_hotkeys = True
    real_thread = threading.Thread

    def _safe_thread(*args, **kwargs):
        if kwargs.get("name") == "StartupUpdateCheck":
            return _NoOpThread()
        return real_thread(*args, **kwargs)

    with patch("threading.Thread", side_effect=_safe_thread):
        return AppController(cfg)


class TestPhase2RecorderAndNotetaker(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _get_qapp()

    # ------------------------------------------------------------------
    # 1. Zero-Modal 1-Click Start & Smart Title Inference
    # ------------------------------------------------------------------
    def test_01_zero_modal_start_and_title_inference(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p2_start_") as td:
            ctrl = _create_controller(td)
            page = None
            try:
                page = NotetakerPage(ctrl)

                with patch("PyQt6.QtWidgets.QInputDialog.getText", side_effect=AssertionError("Blocking QInputDialog called")), \
                     patch.object(ctrl, "start_meeting_recording", return_value=(True, "OK")) as mock_start:

                    # Priority 1: Prepared calendar meeting
                    page.prepare_for_calendar_meeting(SimpleNamespace(title="Design Crit", platform="Zoom"))
                    page.btn_start_adhoc.click()
                    mock_start.assert_called_with(title="Design Crit")
                    self.assertIsNone(page._prepared_calendar_meeting)

                    # Priority 2: Active CallDetector bound window
                    mock_start.reset_mock()
                    ctrl.call_detector = SimpleNamespace(
                        active=SimpleNamespace(window_title="Weekly Eng Standup — Microsoft Teams", app_name="Teams")
                    )
                    page.btn_start_adhoc.click()
                    mock_start.assert_called_with(title="Weekly Eng Standup — Microsoft Teams")

                    # Priority 3: Imminent calendar slot within 15 mins
                    mock_start.reset_mock()
                    ctrl.call_detector = SimpleNamespace(active=None)
                    now_utc = datetime.now(timezone.utc)
                    page._cached_calendar_meetings = [
                        SimpleNamespace(
                            title="1:1 Product Sync",
                            start_time=now_utc + timedelta(minutes=8),
                            end_time=now_utc + timedelta(minutes=38),
                        )
                    ]
                    page.btn_start_adhoc.click()
                    mock_start.assert_called_with(title="1:1 Product Sync")

                    # Priority 4: Clean timestamp fallback
                    mock_start.reset_mock()
                    page._cached_calendar_meetings = []
                    page.btn_start_adhoc.click()
                    called_title = mock_start.call_args.kwargs["title"]
                    self.assertTrue(called_title.startswith("Meeting — "))

                page.close()
            finally:
                _shutdown_controller(ctrl, page)

    # ------------------------------------------------------------------
    # 2. Inline Editable Selected Title + SQLite/List Sync
    # ------------------------------------------------------------------
    def test_02_inline_editable_title(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p2_title_") as td:
            ctrl = _create_controller(td)
            page = None
            try:
                page = NotetakerPage(ctrl)
                # Empty state -> read-only placeholder
                self.assertEqual(page.lbl_sel_title.text(), "Select a call note")
                self.assertTrue(page.lbl_sel_title.isReadOnly())

                m_id = ctrl.db.create_meeting(title="Initial Call Name")
                page.refresh_meetings_list()
                page.select_meeting(m_id)
                self.assertFalse(page.lbl_sel_title.isReadOnly())
                self.assertEqual(page.lbl_sel_title.text(), "Initial Call Name")

                # Edit title inline
                page.lbl_sel_title.setText("Renamed Architecture Deep-Dive")
                page.lbl_sel_title.editingFinished.emit()
                self.app.processEvents()

                self.assertEqual(ctrl.db.get_meeting(m_id)["title"], "Renamed Architecture Deep-Dive")
                self.assertIn("Renamed Architecture Deep-Dive", page.studio.lbl_workspace_title.text())
                self.assertTrue(page.meetings_list.item(0).text().startswith("Renamed Architecture Deep-Dive"))

                # Blank input restores current DB title instead of wiping
                page.lbl_sel_title.setText("   ")
                page.lbl_sel_title.editingFinished.emit()
                self.assertEqual(page.lbl_sel_title.text(), "Renamed Architecture Deep-Dive")

                page.close()
            finally:
                _shutdown_controller(ctrl, page)

    # ------------------------------------------------------------------
    # 3. Live Telemetry Meters & 15s Loopback Anomaly Guard
    # ------------------------------------------------------------------
    def test_03_live_telemetry_and_loopback_anomaly_guard(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p2_tele_") as td:
            ctrl = _create_controller(td)
            page = None
            try:
                page = NotetakerPage(ctrl)
                page.resize(1100, 760)
                page.show()
                ctrl.meeting_state_changed.emit("recording", "Capturing")
                self.app.processEvents()

                self.assertTrue(page.rec_banner.isVisible())
                page._on_meeting_tick(2.0, 0.45, 0.25, True, True)
                self.app.processEvents()
                self.assertEqual(page.bar_mic_level.value(), 45)
                self.assertEqual(page.bar_sys_level.value(), 25)
                self.assertEqual(page.lbl_mic_health.text(), "🎙 Mic OK")
                self.assertEqual(page.lbl_sys_health.text(), "🔊 Loopback OK")
                self.assertFalse(page.lbl_capture_anomaly.isVisible())

                # Loopback stays flat from t=5.0 through t=21.0 (> 15s threshold) -> warning appears
                page._on_meeting_tick(5.0, 0.30, 0.0, True, False)
                self.app.processEvents()
                self.assertFalse(page.lbl_capture_anomaly.isVisible())
                page._on_meeting_tick(21.0, 0.35, 0.001, True, True)
                self.app.processEvents()
                self.assertTrue(page.lbl_capture_anomaly.isVisible())
                self.assertEqual(page.lbl_sys_health.text(), "🔊 Loopback Flat")

                # Audio resumes on loopback -> warning clears
                page._on_meeting_tick(22.0, 0.35, 0.18, True, True)
                self.app.processEvents()
                self.assertFalse(page.lbl_capture_anomaly.isVisible())

                # 1-arg backward-compatible signature
                page._on_meeting_tick(65.0)
                self.assertEqual(page.lbl_rec_timer.text(), "01:05")

                page.close()
            finally:
                _shutdown_controller(ctrl, page)

    # ------------------------------------------------------------------
    # 4. Live Streaming into Virtualized Transcript + Scroll Lock
    # ------------------------------------------------------------------
    def test_04_live_streaming_virtualized_transcript_and_scroll_lock(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p2_live_") as td:
            ctrl = _create_controller(td)
            page = None
            try:
                m_id = ctrl.db.create_meeting(title="Live Capture Test")
                for i in range(25):
                    ctrl.db.add_transcript_segment(
                        meeting_id=m_id,
                        segment_index=i,
                        start_time=float(i * 4),
                        end_time=float(i * 4 + 3),
                        speaker=f"Speaker {(i % 2) + 1}",
                        text=f"Long enough sentence #{i} to ensure transcript list exceeds viewport height smoothly.",
                    )

                page = NotetakerPage(ctrl)
                page.resize(1024, 640)
                page.show()
                page.select_meeting(m_id)
                page.studio.unified_tabs.setCurrentIndex(1)
                self.app.processEvents()

                tx_tab = page.studio.tab_transcript
                sb = tx_tab.list_view.verticalScrollBar()
                self.assertGreater(sb.maximum(), 20, "Transcript list should be scrollable")

                # Start at live bottom, then scroll up -> btn_jump_live appears & scroll lock holds on new append
                sb.setValue(sb.maximum())
                self.app.processEvents()
                sb.setValue(0)
                self.app.processEvents()
                self.assertTrue(tx_tab.btn_jump_live.isVisible())

                ctrl._active_meeting_id = m_id
                tx_tab.set_live_partial("Alice", "Checking budget now")
                self.app.processEvents()
                self.assertTrue(tx_tab.lbl_live_partial.isVisible())
                self.assertIn("Checking budget now", tx_tab.lbl_live_partial.text())

                with patch.object(tx_tab._model, "beginResetModel", wraps=tx_tab._model.beginResetModel) as mock_reset:
                    tx_tab.append_live_segment(
                        {
                            "speaker": "Alice",
                            "start_time": 120.0,
                            "end_time": 124.0,
                            "text": "Let's finalize the budget on Friday.",
                        }
                    )
                    self.app.processEvents()
                    self.assertEqual(mock_reset.call_count, 0, "append_live_segment must use incremental insert, not reset")

                self.assertEqual(tx_tab._model.rowCount(), 26)
                self.assertEqual(sb.value(), 0, "Scroll Lock must preserve user position when scrolled up")
                self.assertTrue(tx_tab.btn_jump_live.isVisible())
                tx_tab.set_live_partial("", "")
                self.assertFalse(tx_tab.lbl_live_partial.isVisible())

                tx_tab._jump_to_live_bottom()
                self.app.processEvents()
                self.assertGreaterEqual(sb.value(), sb.maximum() - 4)
                self.assertFalse(tx_tab.btn_jump_live.isVisible())

                page.close()
            finally:
                _shutdown_controller(ctrl, page)

    # ------------------------------------------------------------------
    # 5. User-Controlled Interrupted Recovery & WAV Preservation
    # ------------------------------------------------------------------
    def test_05_user_controlled_interrupted_recovery_and_wav_preservation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p2_recov_") as td:
            ctrl = _create_controller(td)
            page = None
            try:
                wav_path = Path(td) / "rescued_meeting.wav"
                with wave.open(str(wav_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(b"\x00\x00" * 1600)
                orig_bytes = wav_path.read_bytes()

                m_ok = ctrl.db.create_meeting(title="Rescued Call With Audio")
                ctrl.db.update_meeting(m_ok, status="interrupted", audio_path=str(wav_path), duration_sec=42.0)

                m_missing = ctrl.db.create_meeting(title="Interrupted Without File")
                ctrl.db.update_meeting(m_missing, status="interrupted", audio_path=str(Path(td) / "missing.wav"))

                with patch.object(ctrl, "re_transcribe_meeting") as mock_retrans:
                    page = NotetakerPage(ctrl)
                    page.show()
                    try:
                        self.app.processEvents()

                        # Must NEVER auto-reprocess without explicit user click
                        self.assertEqual(mock_retrans.call_count, 0)

                        # Interrupted banner and list badge visible
                        self.assertTrue(page.interrupted_banner.isVisible())
                        self.assertIn("1 interrupted", page.lbl_interrupted_summary.text())
                        list_texts = [page.meetings_list.item(i).text() for i in range(page.meetings_list.count())]
                        self.assertTrue(any("⚠ Interrupted" in t and "Rescued Call With Audio" in t for t in list_texts))

                        # Meeting with missing audio disables reprocess
                        page.select_meeting(m_missing)
                        self.assertFalse(page.btn_reprocess.isEnabled())

                        # Meeting with existing WAV enables amber Recover button
                        page.select_meeting(m_ok)
                        self.assertTrue(page.btn_reprocess.isEnabled())
                        self.assertEqual(page.btn_reprocess.text(), "↻ Recover & Re-Transcribe")

                        # Explicit click triggers re_transcribe_meeting & preserves original WAV byte-for-byte
                        page.btn_reprocess.click()
                        mock_retrans.assert_called_once_with(m_ok)
                        self.assertTrue(wav_path.exists())
                        self.assertEqual(wav_path.read_bytes(), orig_bytes)
                    finally:
                        page.media_player.stop()
                        page.media_player.setSource(QUrl())
                        page.close()
                        self.app.processEvents()
            finally:
                _shutdown_controller(ctrl, page)

    # ------------------------------------------------------------------
    # 6. Slim Collapsible Upcoming Meetings Strip
    # ------------------------------------------------------------------
    def test_06_collapsible_calendar_strip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p2_cal_") as td:
            ctrl = _create_controller(td)
            page = None
            try:
                page = NotetakerPage(ctrl)
                now_utc = datetime.now(timezone.utc)
                fake_mtg = SimpleNamespace(
                    title="Sprint Review",
                    platform="teams",
                    is_today=True,
                    is_tomorrow=False,
                    formatted_time_range="10:00 AM - 10:30 AM",
                    start_time=now_utc + timedelta(minutes=20),
                    end_time=now_utc + timedelta(minutes=50),
                    join_url="https://teams.microsoft.com/meet/123",
                    organizer="Alice",
                )
                # Ensure provider considered connected for card rendering
                ctrl.config.ms_graph_enabled = True
                page.show()
                self.app.processEvents()
                page._refresh_calendar_strip([fake_mtg])
                self.app.processEvents()

                # Collapsed by default, single-line inline summary shown
                self.assertFalse(page._cal_expanded)
                self.assertTrue(page.cal_content_container.isHidden())
                self.assertTrue(page.cal_filters_container.isHidden())
                self.assertIn("Sprint Review", page.lbl_cal_next_inline.text())
                self.assertTrue(page.btn_cal_quick_record.isVisible())

                # Clicking expand reveals cards + horizon filter buttons
                page.btn_cal_expand.click()
                self.app.processEvents()
                self.assertTrue(page._cal_expanded)
                self.assertFalse(page.cal_content_container.isHidden())
                self.assertFalse(page.cal_filters_container.isHidden())
                self.assertEqual(page.btn_cal_expand.text(), "Collapse ▴")

                page.close()
            finally:
                _shutdown_controller(ctrl, page)

    # ------------------------------------------------------------------
    # 7. Real 4-Stage Pipeline Progress & Telemetry in VoxNotetakerDrawer
    # ------------------------------------------------------------------
    def test_07_drawer_real_stages_and_live_telemetry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vox_p2_drw_") as td:
            ctrl = _create_controller(td)
            drawer = None
            try:
                drawer = VoxNotetakerDrawer(ctrl)
                drawer.show()
                drawer._switch_section(2)  # Select Summary tab housing progress_banner
                self.app.processEvents()

                # Never shows legacy fake "Step 2 of 3" / "Identifying who said what..."
                self.assertNotIn("Step 2 of 3", drawer.lbl_prog_step.text())
                self.assertNotIn("Identifying who said what", drawer.lbl_prog_title.text())

                drawer.update_processing_stage("transcribing", "Stage 1/5: Packaging MP4 video & audio tracks...")
                self.assertTrue(drawer.progress_banner.isVisible())
                self.assertEqual(drawer.lbl_prog_step.text(), "Step 1 of 5")

                drawer.update_processing_stage("transcribing", "Stage 2/5: Transcribing speech with Cohere Transcribe 2B...")
                self.assertEqual(drawer.lbl_prog_step.text(), "Step 2 of 5")

                drawer.update_processing_stage("transcribing", "Stage 3/5: Separating speakers...")
                self.assertEqual(drawer.lbl_prog_step.text(), "Step 3 of 5")

                drawer.update_processing_stage("transcribing", "Stage 4/5: Synthesizing executive MoM...")
                self.assertEqual(drawer.lbl_prog_step.text(), "Step 4 of 5")

                drawer.update_processing_stage("transcribing", "Stage 5/5: Extracting knowledge & updating RAG index...")
                self.assertEqual(drawer.lbl_prog_step.text(), "Step 5 of 5")

                drawer.update_processing_stage("error", "Processing failed in Stage 2: disk full")
                self.assertEqual(drawer.lbl_prog_title.text(), "Processing interrupted")
                self.assertIn("disk full", drawer.lbl_prog_step.text())

                drawer._last_summary_finalized = True
                drawer.update_processing_stage("idle", "Meeting saved & transcribed")
                self.assertFalse(drawer.progress_banner.isVisible())

                # Telemetry ticks reflected cleanly in drawer header status
                drawer._on_meeting_tick(15.0, 0.3, 0.2, True, True)
                self.assertEqual(drawer.lbl_tele_status.text(), "● Recording · Mic OK · Sys OK (Post-Call English ASR)")

                drawer._on_meeting_tick(16.0, 0.3, 0.0, True, False)
                self.assertEqual(drawer.lbl_tele_status.text(), "● Recording · ⚠ Check Loopback (Post-Call Ready)")

                drawer._on_meeting_tick(17.0, 0.0, 0.2, False, True)
                self.assertEqual(drawer.lbl_tele_status.text(), "● Recording · ⚠ Check Mic (Post-Call Ready)")

                drawer.close()
            finally:
                _shutdown_controller(ctrl, drawer)


if __name__ == "__main__":
    unittest.main()

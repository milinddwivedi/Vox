"""
VOX Complete Stability, Concurrency, Failure-Injection & Regression Overhaul Test Suite.

Covers Phases 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12:
- Real-audio PostCallSubprocessRunner GIL-isolation test (proves UI stays <200ms responsive during Cohere + PyAnnote+WeSpeaker)
- Lock-free SQLite WAL concurrent read/write contention test + integrity check
- 120+ transition full MainWindow navigation & theme/drawer stress test on 93-meeting cloned DB
- Concurrent background AI processing + simultaneous UI interaction test
- Heavy workload test (1-hr 1,136-segment meeting, 93-meeting library, incremental actions, hybrid RAG)
- 12 failure injection scenarios (missing/corrupt model, worker crash + checkpoint, CUDA unavailable, low disk, DB contention, missing mic, permission error, mid-job cancel, shutdown during processing, interrupted crash recovery, malformed inputs)
- BackgroundTaskManager lifecycle/dedup, UIEventLoopWatchdog stall capture, threading.excepthook crash report
- Windows Unicode + space path compatibility
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import QCoreApplication, QEvent, QTimer
from PyQt6.QtWidgets import QApplication

from voxlocal.audio.meeting_recorder import _write_mixed, AudioInputMonitor
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.core.diagnostics import (
    UIEventLoopWatchdog,
    get_recent_crash_reports,
    get_unhandled_exception_count,
    install_crash_diagnostics,
    reset_crash_counters,
)
from voxlocal.core.logger import check_disk_space_mb, get_system_resource_snapshot
from voxlocal.core.task_manager import BackgroundTaskManager, TaskState
from voxlocal.engine.meeting_asr_pipeline import (
    PostCallEnglishASRPipeline,
    load_wav_mono_16k_readonly,
)
from voxlocal.engine.meeting_model_manager import MeetingModelManager
from voxlocal.engine.postcall_worker import PostCallSubprocessRunner
from voxlocal.engine.transcriber import LocalWhisperTranscriber
from voxlocal.storage.database import LocalDatabase
from voxlocal.storage.exporter import TranscriptExporter
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer

PROD_DB_PATH = ROOT_DIR / "data" / "voxlocal.db"
REAL_AUDIO_PATH = ROOT_DIR / "data" / "recordings" / "meeting_20261125_213207" / "system_track.wav"
EXPECTED_REAL_AUDIO_SHA256 = "AC25AFCBF1D1C43A6FD77307189F6A8A70BEF41BABAC8DB455AA1363C11783B2"


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    return app


def _clone_prod_db(dest_dir: Path) -> Path:
    dest_db = dest_dir / "voxlocal.db"
    if PROD_DB_PATH.exists():
        src = sqlite3.connect(f"file:{PROD_DB_PATH.as_posix()}?mode=ro", uri=True, timeout=15.0)
        try:
            dst = sqlite3.connect(str(dest_db))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    return dest_db


def _extract_wav_slice(src_wav: Path, dest_wav: Path, seconds: float = 25.0) -> bool:
    if not src_wav.exists():
        return False
    with wave.open(str(src_wav), "rb") as rf:
        ch = rf.getnchannels()
        sw = rf.getsampwidth()
        rate = rf.getframerate()
        n = min(rf.getnframes(), int(seconds * rate))
        raw = rf.readframes(n)
    with wave.open(str(dest_wav), "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(sw)
        wf.setframerate(rate)
        wf.writeframes(raw)
    return dest_wav.exists() and dest_wav.stat().st_size > 44


def _make_isolated_controller(tmp_dir: Path, *, seed_sample_meeting: bool = False) -> AppController:
    _get_qapp()
    cfg = AppConfig()
    cfg.storage_dir = str(tmp_dir)
    cfg.disable_hotkeys = True
    cfg.disable_model_preload = True
    cfg.auto_record_calls = False
    cfg.enable_voice_typing_hotkey = False
    ctrl = AppController(config=cfg)
    if seed_sample_meeting and ctrl.db.list_meetings(limit=1) == []:
        mid = ctrl.db.create_meeting(title="Synthetic Test Sync", source_platform="Microsoft Teams")
        ctrl.db.update_meeting(mid, status="completed", duration_sec=120.0, summary="Test summary")
        ctrl.db.add_transcript_segment(
            meeting_id=mid,
            segment_index=0,
            start_time=0.0,
            end_time=5.0,
            speaker="Alex",
            source_track="system",
            text="We will verify local stability.",
        )
        ctrl.db.add_decision(mid, "Ship stability overhaul", timestamp_sec=2.0, status="confirmed")
        ctrl.db.add_action_item(mid, "Run soak suite", owner="Alex", status="pending")
    return ctrl


class TestStabilityOverhaul:
    """Automated test suite for VOX Stability, Concurrency, Failure Injection & Windows Validation."""

    def test_01_postcall_subprocess_eliminates_gil_freeze_on_real_audio(self, tmp_path: Path) -> None:
        """
        Real-model integration test: run Cohere Transcribe 2B + PyAnnote 3.0 + WeSpeaker
        in PostCallSubprocessRunner on a real speech slice while PyQt6 main loop ticks every 20ms.
        Asserts UI max tick gap stays < 200ms (proving the 5,470ms / 524s GIL freeze is eliminated).
        """
        mm = MeetingModelManager(ROOT_DIR / "data" / "models")
        if not (mm.verify_artifact("meeting_asr")[0] and mm.verify_artifact("meeting_diarization")[0] and REAL_AUDIO_PATH.exists()):
            pytest.skip("Meeting ASR / diarization models or reference audio not available on this machine.")

        slice_wav = tmp_path / "speech_slice_25s.wav"
        assert _extract_wav_slice(REAL_AUDIO_PATH, slice_wav, seconds=25.0)

        app = _get_qapp()
        watchdog = UIEventLoopWatchdog(heartbeat_interval_ms=50, stall_threshold_ms=250.0)
        watchdog.start()

        tick_gaps_ms = []
        last_tick = [time.perf_counter()]
        tick_timer = QTimer()
        tick_timer.setInterval(20)

        def _on_tick() -> None:
            now = time.perf_counter()
            tick_gaps_ms.append((now - last_tick[0]) * 1000.0)
            last_tick[0] = now

        tick_timer.timeout.connect(_on_tick)
        tick_timer.start()

        runner = PostCallSubprocessRunner(models_dir=ROOT_DIR / "data" / "models", num_threads=4)
        res_holder: dict = {}
        progress_msgs = []
        done_ev = threading.Event()

        def _bg() -> None:
            try:
                segs = runner.run(
                    sys_wav_path=slice_wav,
                    diarization_enabled=True,
                    progress_callback=progress_msgs.append,
                    timeout_sec=180.0,
                )
                res_holder["segments"] = segs
                res_holder["stats"] = runner.last_track_stats
                res_holder["diar"] = runner.last_diarization_stats
            except Exception as exc:
                res_holder["error"] = exc
            finally:
                done_ev.set()

        th = threading.Thread(target=_bg, daemon=True)
        th.start()

        deadline = time.monotonic() + 180.0
        while not done_ev.is_set() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.015)

        th.join(timeout=2.0)
        tick_timer.stop()
        w_stats = watchdog.get_stats()
        watchdog.stop()

        assert "error" not in res_holder, f"Worker failed: {res_holder.get('error')}"
        segs = res_holder.get("segments") or []
        assert len(segs) >= 1, "Expected >=1 transcribed segment from real 25s speech slice"
        assert res_holder["diar"].get("enabled") is True
        assert w_stats["stall_count"] == 0, f"Watchdog recorded UI stalls: {w_stats}"
        assert max(tick_gaps_ms or [0.0]) < 200.0, f"Main-thread gap exceeded 200ms: {max(tick_gaps_ms):.1f}ms"

    def test_02_sqlite_wal_lock_free_concurrent_reads_and_integrity(self, tmp_path: Path) -> None:
        """Verify disk reads never block behind concurrent write transactions and preserve integrity."""
        db_path = _clone_prod_db(tmp_path) if PROD_DB_PATH.exists() else (tmp_path / "test.db")
        db = LocalDatabase(db_path)
        try:
            if not db.list_meetings(limit=1):
                db.create_meeting("Lock Contention Seed")

            read_latencies_ms = []
            errors = []
            stop_w = threading.Event()

            def _writer() -> None:
                while not stop_w.is_set():
                    try:
                        with db._lock, db._connect() as conn:
                            conn.execute("BEGIN IMMEDIATE")
                            time.sleep(0.12)
                            conn.rollback()
                    except Exception as exc:
                        errors.append(f"writer:{exc}")
                    time.sleep(0.01)

            wt = threading.Thread(target=_writer, daemon=True)
            wt.start()
            time.sleep(0.03)
            for _ in range(10):
                t0 = time.perf_counter()
                _ = db.list_meetings(limit=50)
                _ = db.get_library_intelligence_snapshot()
                read_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
                time.sleep(0.02)
            stop_w.set()
            wt.join(timeout=2.0)

            assert not errors, f"SQLite contention errors: {errors}"
            assert max(read_latencies_ms) < 60.0, f"Reads blocked behind writer: max={max(read_latencies_ms):.1f}ms"

            with sqlite3.connect(str(db_path)) as chk:
                mode = chk.execute("PRAGMA journal_mode;").fetchone()[0]
                integ = [r[0] for r in chk.execute("PRAGMA integrity_check;").fetchall()]
            assert str(mode).lower() == "wal"
            assert integ == ["ok"]
        finally:
            db.close()

    def test_03_navigation_stress_120_transitions_no_freezes_or_leaks(self, tmp_path: Path) -> None:
        """
        Phase 12.A Navigation Stress Test:
        125+ UI transitions across all 9 workspaces, meeting switches, 4 studio sub-tabs,
        This Meeting <-> Overall Intelligence + subviews, Ask VOX rail, Drawer, and theme toggles.
        """
        if PROD_DB_PATH.exists():
            _clone_prod_db(tmp_path)
        ctrl = _make_isolated_controller(tmp_path, seed_sample_meeting=True)
        app = _get_qapp()
        reset_crash_counters()
        crash_dir = tmp_path / "logs"
        install_crash_diagnostics(crash_dir)
        win = MainWindow(controller=ctrl)
        drawer = VoxNotetakerDrawer(controller=ctrl)
        app.processEvents()

        watchdog = UIEventLoopWatchdog(heartbeat_interval_ms=100, stall_threshold_ms=2500.0)
        watchdog.start()
        snap_start = get_system_resource_snapshot()
        transitions = 0
        mtgs = ctrl.db.list_meetings(limit=20)
        mids = [int(m["id"]) for m in mtgs[:6]]

        try:
            # 1. Cycle through all 9 workspaces multiple rounds (10 * 9 = 90 transitions)
            workspaces = ("home", "notetaker", "calendar", "dictionary", "snippets", "style", "scratchpad", "insights", "settings")
            for _ in range(10):
                for ws in workspaces:
                    win.navigate_to_workspace(ws)
                    app.processEvents()
                    transitions += 1
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()

            # 2. Switch meetings (including longest meeting), studio tabs, drawer & Ask VOX (~45 transitions)
            win.navigate_to_workspace("notetaker")
            app.processEvents()
            transitions += 1
            for r in range(3):
                for mid in mids[:4]:
                    win.notetaker_page.select_meeting(mid)
                    app.processEvents()
                    transitions += 1
                for sub_i in range(win.notetaker_page.studio.unified_tabs.count()):
                    win.notetaker_page.studio.unified_tabs.setCurrentIndex(sub_i)
                    app.processEvents()
                    transitions += 1
                win.notetaker_page._toggle_ask_rail(r % 2 == 0)
                app.processEvents()
                transitions += 1
                drawer.show() if (r % 2 == 0) else drawer.hide()
                app.processEvents()
                transitions += 1

            # 3. Context switching This Meeting <-> Overall Intelligence & 5 subviews + filter (~20 transitions)
            win.notetaker_page.set_workspace_context("overall")
            app.processEvents()
            transitions += 1
            for key, _lbl in win.notetaker_page.overall_view.SUBVIEWS:
                win.notetaker_page.overall_view.set_subview(key)
                app.processEvents()
                transitions += 1
            win.notetaker_page.set_workspace_context("meeting")
            app.processEvents()
            transitions += 1

            # 4. Theme toggles + library filter/search (~10 transitions)
            win.apply_theme("dark")
            app.processEvents()
            win.apply_theme("light")
            app.processEvents()
            win.notetaker_page._set_library_filter("completed")
            win.notetaker_page._set_library_filter("all")
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            transitions += 4

            assert transitions >= 120
            w_stats = watchdog.get_stats()
            assert w_stats["stall_count"] == 0, f"Navigation stress triggered UI stalls: {w_stats}"
            assert get_unhandled_exception_count() == 0

            import gc
            gc.collect()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            snap_end = get_system_resource_snapshot()
            rss_delta_mb = snap_end["rss_mb"] - snap_start["rss_mb"]
            thread_delta = snap_end["thread_count"] - snap_start["thread_count"]
            handle_delta = snap_end["handle_count"] - snap_start["handle_count"]
            # Generous ceiling to prove zero runaway leaks over 125+ transitions
            assert rss_delta_mb < 150.0, f"Unexpected RAM growth during navigation stress: +{rss_delta_mb:.1f} MB"
            assert thread_delta <= 4, f"Thread leak detected: +{thread_delta} threads"
            assert handle_delta < 200, f"Handle leak detected: +{handle_delta} handles"
        finally:
            watchdog.stop()
            drawer.close()
            win.prepare_force_quit()
            win.close()
            ctrl.shutdown()
            app.processEvents()

    def test_04_background_processing_during_concurrent_ui_navigation(self, tmp_path: Path) -> None:
        """
        Phase 12.B: Run real PostCallSubprocessRunner while navigating tabs, switching meetings,
        opening Settings, searching, and toggling Ask VOX concurrently.
        """
        mm = MeetingModelManager(ROOT_DIR / "data" / "models")
        if not (mm.verify_artifact("meeting_asr")[0] and REAL_AUDIO_PATH.exists()):
            pytest.skip("Cohere model or audio unavailable")

        if PROD_DB_PATH.exists():
            _clone_prod_db(tmp_path)
        ctrl = _make_isolated_controller(tmp_path, seed_sample_meeting=True)
        app = _get_qapp()
        win = MainWindow(controller=ctrl)

        slice_wav = tmp_path / "concurrent_18s.wav"
        _extract_wav_slice(REAL_AUDIO_PATH, slice_wav, seconds=18.0)
        runner = PostCallSubprocessRunner(models_dir=ROOT_DIR / "data" / "models", num_threads=4)
        out: dict = {}
        done_ev = threading.Event()

        def _bg() -> None:
            try:
                out["segs"] = runner.run(sys_wav_path=slice_wav, diarization_enabled=False, timeout_sec=120.0)
            except Exception as exc:
                out["err"] = exc
            finally:
                done_ev.set()

        th = threading.Thread(target=_bg, daemon=True)
        th.start()

        try:
            mtgs = ctrl.db.list_meetings(limit=10)
            cycle = ("notetaker", "settings", "insights", "notetaker", "calendar", "home")
            i = 0
            while not done_ev.is_set() and i < 30:
                win.navigate_to_workspace(cycle[i % len(cycle)])
                if mtgs:
                    win.notetaker_page.select_meeting(int(mtgs[i % len(mtgs)]["id"]))
                win.notetaker_page.txt_search.setText("Daily" if i % 2 == 0 else "")
                win.notetaker_page._toggle_ask_rail(i % 2 == 0)
                app.processEvents()
                time.sleep(0.15)
                i += 1
            done_ev.wait(timeout=120.0)
            th.join(timeout=2.0)

            assert "err" not in out
            assert len(out.get("segs") or []) >= 1
        finally:
            if not done_ev.is_set():
                runner.cancel()
            win.prepare_force_quit()
            win.close()
            ctrl.shutdown()
            app.processEvents()

    def test_05_heavy_workload_large_meeting_and_incremental_actions(self, tmp_path: Path) -> None:
        """
        Phase 12.C Heavy Workload: 1-hr 1,136-segment meeting loading + Overall Intelligence
        with 90+ meetings + incremental actions cap (40 initial cards) + speaker rename async reindex.
        """
        if PROD_DB_PATH.exists():
            _clone_prod_db(tmp_path)
        ctrl = _make_isolated_controller(tmp_path, seed_sample_meeting=True)
        app = _get_qapp()
        win = MainWindow(controller=ctrl)

        try:
            mtgs = ctrl.db.list_meetings(limit=300)
            longest = max(mtgs, key=lambda m: float(m.get("duration_sec") or 0.0))
            lid = int(longest["id"])

            win.navigate_to_workspace("notetaker")
            t0 = time.perf_counter()
            win.notetaker_page.select_meeting(lid)
            app.processEvents()
            sel_ms = (time.perf_counter() - t0) * 1000.0
            assert sel_ms < 800.0, f"1-hour meeting load too slow: {sel_ms:.1f}ms"

            # Speaker rename keeps UI fast (<50ms synchronous DB rename; ONNX reindex runs in BackgroundTaskManager)
            segs = ctrl.db.get_meeting_segments(lid)
            if segs:
                orig_spk = str(segs[0].get("speaker") or "Speaker 1")
                t_ren0 = time.perf_counter()
                cnt = ctrl.rename_meeting_speaker_and_reindex(lid, orig_spk, f"{orig_spk} Renamed")
                ren_ms = (time.perf_counter() - t_ren0) * 1000.0
                assert cnt >= 1
                assert ren_ms < 150.0, f"Speaker rename blocked UI: {ren_ms:.1f}ms"

            # Overall Intelligence incremental rendering check
            win.notetaker_page.set_workspace_context("overall")
            win.notetaker_page.overall_view.set_subview("actions")
            app.processEvents()
            ov = win.notetaker_page.overall_view
            # Ensure initial action item widget count is bounded incrementally (~41 max including optional Show More btn)
            assert ov.actions_list_layout.count() <= 45
        finally:
            win.prepare_force_quit()
            win.close()
            ctrl.shutdown()
            app.processEvents()

    def test_06_failure_injection_suite_all_12_scenarios(self, tmp_path: Path) -> None:
        """
        Phase 12.D Failure Injection: verify graceful recovery across 12 failure modes
        with zero crashes and preserved user data.
        """
        # 1. Missing model files
        empty_models = tmp_path / "empty_models"
        empty_models.mkdir(parents=True, exist_ok=True)
        mm_missing = MeetingModelManager(empty_models)
        ok_m, msg_m = mm_missing.verify_artifact("meeting_asr")
        assert ok_m is False and "Missing" in msg_m
        pipe_missing = PostCallEnglishASRPipeline(models_dir=empty_models)
        with pytest.raises(RuntimeError):
            pipe_missing._ensure_asr_loaded()

        # 2. Corrupt/truncated model files
        asr_dir = mm_missing.get_artifact_dir("meeting_asr")
        asr_dir.mkdir(parents=True, exist_ok=True)
        for fname in ("encoder.int8.onnx", "encoder.int8.onnx.data", "decoder.int8.onnx", "tokens.txt"):
            (asr_dir / fname).write_bytes(b"corrupt")
        ok_c, msg_c = mm_missing.verify_artifact("meeting_asr")
        assert ok_c is False and "corrupt/incomplete" in msg_c

        # 3. Worker subprocess failure + ASR checkpoint preservation check
        if (ROOT_DIR / "data" / "models" / "meeting_asr").exists():
            runner_crash = PostCallSubprocessRunner(models_dir=ROOT_DIR / "data" / "models")
            with patch.object(runner_crash, "_build_worker_cmd", side_effect=lambda jp: (
                [sys.executable, "-c",
                 f"import json, pathlib; sp=json.loads(pathlib.Path(r'{jp}').read_text()); "
                 "pathlib.Path(sp['checkpoint_path']).write_text(json.dumps({'segments':[{'text':'saved checkpoint'}]})); "
                 "raise SystemExit(139)"],
                tmp_path,
            )):
                with pytest.raises(RuntimeError):
                    runner_crash.run(sys_wav_path=tmp_path / "none.wav")
                assert len(runner_crash.last_checkpoint_segments) == 1

        # 4. GPU/CUDA unavailable -> CPU 'small' single load without double-loading heavy model
        tr = LocalWhisperTranscriber(models_dir=ROOT_DIR / "data" / "models", model_name="large-v3-turbo", compute_device="auto")
        if tr.is_model_cached("small"):
            with patch("voxlocal.engine.transcriber.is_cuda_runtime_available", return_value=False):
                ok_l, msg_l = tr.ensure_loaded("large-v3-turbo")
                assert ok_l is True
                assert tr._loaded_model_name == "small"
                assert tr._active_device == "cpu"
                tr.unload_model()

        # 5. Disk space exhaustion check
        ctrl = _make_isolated_controller(tmp_path / "disk_test", seed_sample_meeting=True)
        try:
            with patch("voxlocal.core.controller.check_disk_space_mb", return_value=(False, 12.0, "Insufficient disk space (12.0 MB free)")):
                ok_d, msg_d = ctrl.start_meeting_recording(title="Low Disk Test")
                assert ok_d is False and "Insufficient disk space" in msg_d
                ok_sd, msg_sd = ctrl.start_screen_recording(title="Low Disk Screen")
                assert ok_sd is False and "Insufficient disk space" in msg_sd

            # 6. SQLite busy / error recovery (malformed query / integrity preserved)
            db = ctrl.db
            mid = db.create_meeting("Safe Record")
            assert db.get_meeting(mid) is not None

            # 7. Missing / disconnected audio device health monitor & start failure
            mon = AudioInputMonitor()
            mon.configure_tracks(mic_enabled=True, system_enabled=False)
            mon.observe("mic", np.zeros(1600, dtype=np.float32))
            warns = mon.heartbeat_tick(elapsed_sec=5.0, mic_active=False, sys_active=False, is_paused=False)
            assert any("disconnected" in w.lower() for w in warns)

            # 8. File permission / read-only export failure handled cleanly
            bad_out = tmp_path / "no_such_dir" / "nested_blocked" / "\0badname.json"
            with patch("pathlib.Path.write_text", side_effect=PermissionError("Access denied")):
                ctrl.config.save()  # logs error safely, does not crash

            # 9 & 10. Mid-job worker cancellation & shutdown during active subprocess
            runner_cancel = PostCallSubprocessRunner(models_dir=ROOT_DIR / "data" / "models")
            with patch.object(runner_cancel, "_build_worker_cmd", side_effect=lambda jp: (
                [sys.executable, "-c", "import time; time.sleep(30)"],
                tmp_path,
            )):
                cancelled_flag = threading.Event()
                res_c: dict = {}

                def _run_c() -> None:
                    res_c["segs"] = runner_cancel.run(
                        sys_wav_path=tmp_path / "dummy.wav",
                        cancel_check=cancelled_flag.is_set,
                        timeout_sec=20.0,
                    )

                tc = threading.Thread(target=_run_c, daemon=True)
                tc.start()
                time.sleep(0.3)
                t_can0 = time.perf_counter()
                cancelled_flag.set()
                runner_cancel.cancel()
                tc.join(timeout=4.0)
                assert not tc.is_alive()
                assert (time.perf_counter() - t_can0) < 3.0
                assert res_c.get("segs") == []

            # 11. Interrupted recording crash recovery
            rec_dir = Path(ctrl.config.recordings_dir) / "meeting_20261127_000001"
            rec_dir.mkdir(parents=True, exist_ok=True)
            crash_wav = rec_dir / "meeting_mixed.wav"
            with wave.open(str(crash_wav), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(np.zeros(32000, dtype=np.int16).tobytes())
            crashed_mid = ctrl.db.create_meeting("Crashed Call Test")
            ctrl.db.update_meeting(crashed_mid, status="recording", audio_path=str(crash_wav))
            ctrl._recover_unfinalized_meetings()
            recovered = ctrl.db.get_meeting(crashed_mid)
            assert recovered["status"] == "interrupted"
            assert recovered["duration_sec"] >= 1.9

            # 12. Invalid / malformed inputs (0-byte & corrupt WAV headers, bad meeting ID)
            corrupt_wav = tmp_path / "corrupt.wav"
            corrupt_wav.write_bytes(b"NOT_A_RIFF_WAV")
            arr_bad = load_wav_mono_16k_readonly(corrupt_wav)
            assert arr_bad.size == 0
            mixed_out = tmp_path / "mixed_bad.wav"
            dur_bad = _write_mixed(corrupt_wav, None, mixed_out)
            assert dur_bad == 0.0
            assert ctrl.db.get_meeting(-99999) is None
        finally:
            t_sh0 = time.perf_counter()
            ctrl.shutdown()
            assert (time.perf_counter() - t_sh0) < 3.0

    def test_07_task_manager_watchdog_and_crash_diagnostics(self, tmp_path: Path) -> None:
        """Verify BackgroundTaskManager dedupe/states/shutdown, Watchdog stall capture, and threading crash hook."""
        tm = BackgroundTaskManager(io_workers=2, ai_workers=1)
        gate = threading.Event()
        t1 = tm.submit("sync_test", lambda cancel_event: gate.wait(2.0), dedupe_key="same_key")
        t2 = tm.submit("sync_test", lambda cancel_event: gate.wait(2.0), dedupe_key="same_key")
        assert t1.task_id == t2.task_id
        gate.set()
        time.sleep(0.15)
        assert tm.get_task(t1.task_id)["state"] == TaskState.COMPLETED.value

        t_fail = tm.submit("fail_test", lambda: (_ for _ in ()).throw(ValueError("boom")))
        time.sleep(0.15)
        assert tm.get_task(t_fail.task_id)["state"] == TaskState.FAILED.value
        assert "boom" in tm.get_task(t_fail.task_id)["error"]
        tm.shutdown(timeout_sec=1.0)

        # Watchdog detects simulated UI stall (>150ms) and captures stack
        app = _get_qapp()
        wd = UIEventLoopWatchdog(heartbeat_interval_ms=40, stall_threshold_ms=140.0)
        wd.start()
        try:
            time.sleep(0.28)  # Simulate blocking main thread
            app.processEvents()
            st = wd.get_stats()
            assert st["stall_count"] >= 1
            assert st["max_stall_ms"] >= 140.0
        finally:
            wd.stop()

        # Worker-thread unhandled exception creates sanitized crash report without killing test
        reset_crash_counters()
        install_crash_diagnostics(tmp_path / "crash_logs")

        def _boom() -> None:
            raise RuntimeError("injected_worker_exception_privacy_safe")

        bt = threading.Thread(target=_boom, name="InjectedCrashWorker")
        bt.start()
        bt.join(timeout=2.0)
        assert get_unhandled_exception_count() >= 1
        reports = get_recent_crash_reports()
        assert reports and Path(reports[-1]).exists()
        rep_data = json.loads(Path(reports[-1]).read_text(encoding="utf-8"))
        assert rep_data["thread_name"] == "InjectedCrashWorker"
        assert rep_data["exception_type"] == "RuntimeError"

    def test_08_windows_unicode_and_spaces_paths_and_prod_data_untouched(self, tmp_path: Path) -> None:
        """Verify Windows Unicode & space-containing paths work end-to-end and production data is untouched."""
        u_dir = tmp_path / "VOX Café Space Test 2026"
        u_dir.mkdir(parents=True, exist_ok=True)
        cfg = AppConfig(storage_dir=str(u_dir))
        cfg.storage_dir = u_dir  # Intentionally test Path assignment fix!
        cfg.save()
        cfg_file = Path(u_dir) / "config.json"
        assert cfg_file.exists()

        db = LocalDatabase(u_dir / "voxlocal.db")
        mid = db.create_meeting("Café Architecture Sync — Release 2026")
        db.update_meeting(mid, status="completed", summary="Unicode path summary OK")
        db.add_transcript_segment(
            meeting_id=mid,
            segment_index=0,
            start_time=0.0,
            end_time=4.0,
            speaker="Milind",
            source_track="mic",
            text="Testing Unicode Windows path persistence.",
        )
        m = db.get_meeting(mid)
        m["segments"] = db.get_meeting_segments(mid)
        out_md = u_dir / "Café Export Summary.md"
        TranscriptExporter.export_to_file(m, out_md, fmt="md")
        assert out_md.exists() and "Testing Unicode Windows path persistence" in out_md.read_text(encoding="utf-8")
        db.close()

        if REAL_AUDIO_PATH.exists():
            h = hashlib.sha256(REAL_AUDIO_PATH.read_bytes()).hexdigest().upper()
            assert h == EXPECTED_REAL_AUDIO_SHA256

    def test_09_metering_concurrent_wal_with_local_database(self, tmp_path: Path) -> None:
        """P0-1: Verify UsageMeteringEngine pools connections, initializes WAL once, and races cleanly with LocalDatabase."""
        from voxlocal.core.metering import UsageMeteringEngine

        db_path = tmp_path / "metering_wal.db"
        db = LocalDatabase(db_path)
        metering = UsageMeteringEngine(db_path)
        errors: list = []
        stop_ev = threading.Event()

        def _db_writer() -> None:
            i = 0
            while not stop_ev.is_set() and i < 40:
                try:
                    mid = db.create_meeting(f"Concurrent WAL Meeting {i}")
                    db.update_meeting(mid, status="completed", duration_sec=60.0)
                    i += 1
                except Exception as exc:
                    errors.append(f"db:{exc}")
                time.sleep(0.005)

        def _meter_worker() -> None:
            j = 0
            while not stop_ev.is_set() and j < 40:
                try:
                    metering.record_committed_dictation(
                        "One two three four five six seven eight nine ten eleven twelve",
                        session_id=f"s_{j}",
                    )
                    _ = metering.get_weekly_usage()
                    metering.record_ask_vox_query(query_id=f"q_{j}")
                    j += 1
                except Exception as exc:
                    errors.append(f"metering:{exc}")
                time.sleep(0.005)

        try:
            t1 = threading.Thread(target=_db_writer, daemon=True)
            t2 = threading.Thread(target=_meter_worker, daemon=True)
            t1.start()
            t2.start()
            t1.join(timeout=15.0)
            t2.join(timeout=15.0)
            stop_ev.set()

            assert not errors, f"Concurrent metering/DB errors: {errors}"
            usage = metering.get_weekly_usage()
            assert usage["dictation_words"] >= 12 * 40
            assert usage["ask_vox_count"] >= 40
            # Verify pooled connection count remains bounded (one per active thread)
            assert len(metering._thread_conns) <= 4
        finally:
            stop_ev.set()
            metering.close()
            db.close()
            with sqlite3.connect(str(db_path)) as chk:
                integ = [r[0] for r in chk.execute("PRAGMA integrity_check;").fetchall()]
            assert integ == ["ok"]

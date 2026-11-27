"""
Repeatable VOX Endurance & Soak Test Harness (Phase 12.E).

Usage:
  .venv\\Scripts\\python.exe tests\\soak_endurance_test.py --duration-minutes 2 --out soak_report.json
  .venv\\Scripts\\python.exe tests\\soak_endurance_test.py --duration-minutes 60 --out soak_60m_report.json

Safely operates on an isolated temporary clone of data/voxlocal.db & a temporary speech slice;
never modifies production user recordings, database, or config.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import QCoreApplication, QEvent, QTimer
from PyQt6.QtWidgets import QApplication

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.core.diagnostics import (
    UIEventLoopWatchdog,
    get_unhandled_exception_count,
    install_crash_diagnostics,
    reset_crash_counters,
)
from voxlocal.core.logger import get_system_resource_snapshot
from voxlocal.engine.meeting_model_manager import MeetingModelManager
from voxlocal.engine.postcall_worker import PostCallSubprocessRunner
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.notetaker_drawer import VoxNotetakerDrawer


def _extract_wav_slice(src_wav: Path, dest_wav: Path, seconds: float = 15.0, start_sec: float = 12.0) -> bool:
    if not src_wav.exists():
        return False
    with wave.open(str(src_wav), "rb") as rf:
        ch = rf.getnchannels()
        sw = rf.getsampwidth()
        rate = rf.getframerate()
        total_frames = rf.getnframes()
        start_frame = min(total_frames, max(0, int(start_sec * rate)))
        rf.setpos(start_frame)
        raw = rf.readframes(min(total_frames - start_frame, int(seconds * rate)))
    with wave.open(str(dest_wav), "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(sw)
        wf.setframerate(rate)
        wf.writeframes(raw)
    return dest_wav.exists() and dest_wav.stat().st_size > 44


def _write_test_wav(dest_wav: Path, seconds: float = 1.0, rate: int = 16000) -> None:
    dest_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x01\x00" * int(seconds * rate))


def _count_llama_servers() -> int:
    try:
        res = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq llama-server.exe"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        return sum(1 for ln in res.stdout.splitlines() if "llama-server.exe" in ln.lower())
    except Exception:
        return 0


def run_soak(duration_minutes: float = 2.0, with_ai_jobs: bool = True, out_json: str = "") -> int:
    duration_sec = max(10.0, float(duration_minutes) * 60.0)
    tmp_dir = Path(tempfile.mkdtemp(prefix="vox_soak_"))
    app = QApplication.instance() or QApplication(["--platform", "offscreen"])

    reset_crash_counters()
    install_crash_diagnostics(tmp_dir / "logs")

    prod_db = ROOT_DIR / "data" / "voxlocal.db"
    tmp_db = tmp_dir / "voxlocal.db"
    if prod_db.exists():
        src = sqlite3.connect(f"file:{prod_db.as_posix()}?mode=ro", uri=True, timeout=15.0)
        try:
            dst = sqlite3.connect(str(tmp_db))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

    real_wav = ROOT_DIR / "data" / "recordings" / "meeting_20261125_213207" / "system_track.wav"
    slice_wav = tmp_dir / "soak_slice.wav"
    has_slice = _extract_wav_slice(real_wav, slice_wav, seconds=15.0, start_sec=12.0)
    mm = MeetingModelManager(ROOT_DIR / "data" / "models")
    can_run_worker = with_ai_jobs and has_slice and mm.verify_artifact("meeting_asr")[0]

    cfg_path = tmp_dir / "config.json"
    os.environ["VOXLOCAL_CONFIG_PATH"] = str(cfg_path)
    cfg = AppConfig()
    cfg.storage_dir = str(tmp_dir)
    cfg.disable_hotkeys = True
    cfg.disable_model_preload = True
    cfg.auto_record_calls = False
    cfg.enable_voice_typing_hotkey = False
    cfg.save()

    ctrl = AppController(config=cfg)
    if not ctrl.db.list_meetings(limit=1):
        mid = ctrl.db.create_meeting("Soak Seed Meeting")
        ctrl.db.update_meeting(mid, status="completed", duration_sec=60.0)

    win = MainWindow(controller=ctrl)
    drawer = VoxNotetakerDrawer(controller=ctrl)
    watchdog = UIEventLoopWatchdog(app, heartbeat_interval_ms=100, stall_threshold_ms=1500.0)
    watchdog.start()

    max_tick_gap_ms = 0.0
    last_tick = time.perf_counter()
    db_lock_errors = 0
    metering_ops = 0
    metering_lock_errors = 0
    crash_recovery_cycles = 0
    lost_recordings = 0
    ui_errors = 0
    ai_jobs_completed = 0
    ai_jobs_failed = 0
    transitions = 0

    stop_bg = threading.Event()
    ai_busy = threading.Event()
    metering_lock = threading.Lock()
    active_runner: list = [None]
    ai_thread: list = [None]

    def _db_writer_loop() -> None:
        nonlocal db_lock_errors
        n = 0
        while not stop_bg.is_set():
            try:
                note_id = ctrl.db.create_note(f"Soak Note {n}", "body", "body")
                ctrl.db.delete_note(note_id)
                n += 1
            except Exception as exc:
                if "locked" in str(exc).lower():
                    db_lock_errors += 1
            time.sleep(0.35)

    writer_thread = threading.Thread(target=_db_writer_loop, name="SoakDbWriter", daemon=True)
    writer_thread.start()

    def _metering_worker_loop(tid: int) -> None:
        nonlocal metering_ops, metering_lock_errors
        n = 0
        while not stop_bg.is_set():
            try:
                ctrl.metering.record_committed_dictation(
                    f"soak metering check {tid} {n}",
                    session_id=f"soak_{tid}_{n}",
                )
                ctrl.metering.get_usage_snapshot()
                with metering_lock:
                    metering_ops += 1
                n += 1
            except Exception as exc:
                msg = str(exc).lower()
                if "locked" in msg or "malformed" in msg or "operational" in msg or isinstance(exc, sqlite3.Error):
                    with metering_lock:
                        metering_lock_errors += 1
                    print(f"[SOAK METERING ERROR t{tid}] {exc}", file=sys.stderr)
            time.sleep(0.4)

    metering_threads = [
        threading.Thread(target=_metering_worker_loop, args=(tid,), name=f"SoakMeteringWorker-{tid}", daemon=True)
        for tid in (0, 1)
    ]
    for mth in metering_threads:
        mth.start()

    def _spawn_ai_job() -> None:
        nonlocal ai_jobs_completed, ai_jobs_failed
        if not can_run_worker or ai_busy.is_set() or stop_bg.is_set():
            return
        ai_busy.set()

        def _worker() -> None:
            nonlocal ai_jobs_completed, ai_jobs_failed
            runner = PostCallSubprocessRunner(models_dir=ROOT_DIR / "data" / "models", num_threads=4)
            active_runner[0] = runner
            try:
                segs = runner.run(sys_wav_path=slice_wav, diarization_enabled=True, timeout_sec=120.0)
                if stop_bg.is_set():
                    return
                if len(segs) >= 1:
                    ai_jobs_completed += 1
                else:
                    ai_jobs_failed += 1
                    print("[SOAK AI WORKER] Empty transcript segments returned", file=sys.stderr)
            except Exception as exc:
                if not stop_bg.is_set():
                    ai_jobs_failed += 1
                    print(f"[SOAK AI WORKER ERROR] {exc}", file=sys.stderr)
            finally:
                active_runner[0] = None
                ai_busy.clear()

        th = threading.Thread(target=_worker, name="SoakAiWorker", daemon=True)
        ai_thread[0] = th
        th.start()

    tick_timer = QTimer()
    tick_timer.setInterval(50)

    def _tick() -> None:
        nonlocal max_tick_gap_ms, last_tick
        now = time.perf_counter()
        gap = (now - last_tick) * 1000.0
        if gap > max_tick_gap_ms:
            max_tick_gap_ms = gap
        last_tick = now

    tick_timer.timeout.connect(_tick)
    tick_timer.start()

    mtgs = ctrl.db.list_meetings(limit=20)
    mids = [int(m["id"]) for m in mtgs[:6]]
    if ctrl.db.get_meeting(143) is not None and 143 not in mids:
        mids.append(143)
    workspaces = ("home", "notetaker", "calendar", "dictionary", "snippets", "style", "scratchpad", "insights", "settings")

    baseline_snap = get_system_resource_snapshot()
    samples = [{"t_sec": 0.0, **baseline_snap}]
    t0 = time.monotonic()
    last_sample_t = t0
    last_ai_t = -999.0
    last_recovery_t = -175.0

    def _run_crash_recovery_cycle(cycle: int) -> None:
        nonlocal crash_recovery_cycles, lost_recordings
        wav_path = tmp_dir / "recordings" / f"soak_crash_{cycle}.wav"
        _write_test_wav(wav_path, seconds=1.0)
        rec_mid = ctrl.db.create_meeting(f"Soak Interrupted Recovery {cycle}")
        ctrl.db.update_meeting(rec_mid, status="recording", audio_path=str(wav_path))
        unfinalized = ctrl.db.list_unfinalized_meetings()
        found = any(int(m["id"]) == int(rec_mid) for m in unfinalized)
        audio_ok = wav_path.exists() and wav_path.stat().st_size > 44
        if not (found and audio_ok):
            lost_recordings += 1
            print(f"[SOAK RECOVERY ERROR] cycle={cycle} found={found} audio_ok={audio_ok}", file=sys.stderr)
        ctrl.db.update_meeting(rec_mid, status="completed")
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass
        crash_recovery_cycles += 1

    try:
        idx = 0
        while (time.monotonic() - t0) < duration_sec:
            try:
                ws = workspaces[idx % len(workspaces)]
                win.navigate_to_workspace(ws)
                if ws == "notetaker" and mids:
                    win.notetaker_page.select_meeting(mids[idx % len(mids)])
                    sub = win.notetaker_page.studio.unified_tabs
                    sub.setCurrentIndex(idx % sub.count())
                    if idx % 18 == 0:
                        win.notetaker_page.set_workspace_context("overall")
                    else:
                        win.notetaker_page.set_workspace_context("meeting")
                if idx % 25 == 0:
                    drawer.show() if not drawer.isVisible() else drawer.hide()
                if idx % 35 == 10 and mids:
                    ctrl.task_manager.submit(
                        "soak_ask_vox",
                        lambda m_id=mids[0]: ctrl.intelligence.query(
                            "Summarize key decisions", meeting_id=m_id, answer_depth="concise"
                        ),
                        lane="ai",
                        dedupe_key="soak_ask_vox",
                    )
                transitions += 1
            except Exception as exc:
                ui_errors += 1
                print(f"[SOAK UI ERROR] {exc}", file=sys.stderr)

            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            now_m = time.monotonic()
            if (now_m - last_recovery_t) >= 180.0:
                last_recovery_t = now_m
                _run_crash_recovery_cycle(crash_recovery_cycles + 1)
            if can_run_worker and (now_m - last_ai_t) >= 35.0 and not ai_busy.is_set():
                last_ai_t = now_m
                _spawn_ai_job()
            if (now_m - last_sample_t) >= 15.0:
                last_sample_t = now_m
                samples.append({"t_sec": round(now_m - t0, 1), **get_system_resource_snapshot()})
            idx += 1
            time.sleep(0.15)
    finally:
        stop_bg.set()
        tick_timer.stop()
        if active_runner[0] is not None:
            try:
                active_runner[0].cancel()
            except Exception:
                pass
        if ai_thread[0] is not None:
            ai_thread[0].join(timeout=5.0)
        writer_thread.join(timeout=1.5)
        for mth in metering_threads:
            mth.join(timeout=2.0)
        w_stats = watchdog.get_stats()
        watchdog.stop()
        drawer.close()
        win.prepare_force_quit()
        win.close()
        ctrl.shutdown()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        time.sleep(0.5)
        end_snap = get_system_resource_snapshot()
        orphan_llama_servers = _count_llama_servers()

    # Check DB integrity after soak
    db_ok = False
    try:
        with sqlite3.connect(str(tmp_db if tmp_db.exists() else cfg.db_path)) as c:
            db_ok = [r[0] for r in c.execute("PRAGMA integrity_check;").fetchall()] == ["ok"]
    except Exception:
        db_ok = False

    rss_growth_mb = round(end_snap["rss_mb"] - baseline_snap["rss_mb"], 2)
    report = {
        "duration_requested_min": duration_minutes,
        "duration_actual_sec": round(time.monotonic() - t0, 2),
        "transitions_completed": transitions,
        "ai_jobs_completed": ai_jobs_completed,
        "ai_jobs_failed": ai_jobs_failed,
        "db_lock_errors": db_lock_errors,
        "metering_ops": metering_ops,
        "metering_lock_errors": metering_lock_errors,
        "crash_recovery_cycles": crash_recovery_cycles,
        "lost_recordings": lost_recordings,
        "orphan_llama_servers": orphan_llama_servers,
        "ui_errors": ui_errors,
        "unhandled_exceptions": get_unhandled_exception_count(),
        "watchdog_stats": w_stats,
        "max_tick_gap_ms": round(max_tick_gap_ms, 2),
        "baseline_resources": baseline_snap,
        "end_resources": end_snap,
        "rss_growth_mb": rss_growth_mb,
        "db_integrity_ok": db_ok,
        "samples": samples,
    }
    rendered = json.dumps(report, indent=2)
    if out_json:
        Path(out_json).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    shutil.rmtree(tmp_dir, ignore_errors=True)

    passed = (
        db_lock_errors == 0
        and metering_lock_errors == 0
        and lost_recordings == 0
        and orphan_llama_servers == 0
        and ui_errors == 0
        and report["unhandled_exceptions"] == 0
        and ai_jobs_failed == 0
        and w_stats["stall_count"] == 0
        and db_ok
        and rss_growth_mb < 180.0
    )
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VOX Endurance & Soak Test")
    parser.add_argument("--duration-minutes", type=float, default=60.0)
    parser.add_argument("--no-ai-jobs", action="store_true")
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()
    sys.exit(run_soak(duration_minutes=args.duration_minutes, with_ai_jobs=not args.no_ai_jobs, out_json=args.out))

"""
Comprehensive Regression & Integration Suite for VOX v1.0.13:
- Resilient direct updater handoff, space-split path recovery, backup & rollback,
  --verify-runtime, update_status.json startup handshake, themed QMessageBox.
- Recording-first capture isolation, non-destructive DB migration on a real DB copy,
  immediate audio path persistence & playback during post-call transcription,
  provisional preview archive & toggle, CPU post-call Oriserve verifier fallback,
  adaptive pure-English Parakeet upgrade guard, bounded-backlog live preview isolation,
  and offline model downloader safeguards.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
PKG_DIR = ROOT_DIR / "packaging"
for _p in (str(SRC_DIR), str(PKG_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import voxlocal
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController, _polish_final_punctuation
from voxlocal.core.hardware import ExecutionProfile, HardwareProfiler
import voxlocal.engine.onnx_asr_engines as onnx_mod
from voxlocal.engine.onnx_asr_engines import (
    GuardedQwen3OnnxEngine,
    OriserveApexVerifierEngine,
    ParakeetOnnxEngine,
    download_phase2_model,
)
from voxlocal.engine.selective_verifier import SelectiveSecondPassVerifier
from voxlocal.engine.streaming_transcriber import LiveMeetingStreamingTranscriber
from voxlocal.engine.transcriber import LocalWhisperTranscriber
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.main_window import MainWindow
from voxlocal.ui.meeting_studio import VoxInteractiveTranscriptTab
from voxlocal.ui.notetaker_page import NotetakerPage
import vox_setup_installer as installer_mod


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _write_wav(path: Path, samples: np.ndarray, sr: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _make_fake_models(models_dir: Path) -> None:
    q3 = models_dir / "challengers" / "sherpa-onnx-qwen3-asr-0.6b-int8"
    (q3 / "tokenizer").mkdir(parents=True, exist_ok=True)
    for rel in ("conv_frontend.onnx", "encoder.int8.onnx", "decoder.int8.onnx", "tokenizer/vocab.json", "tokenizer/merges.txt"):
        (q3 / rel).write_bytes(b"fake")
    for slug in ("sherpa-onnx-parakeet-tdt-0.6b-v2-int8", "sherpa-onnx-nemotron-streaming-en-0.6b-560ms-int8"):
        d = models_dir / "challengers" / slug
        d.mkdir(parents=True, exist_ok=True)
        for rel in ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"):
            (d / rel).write_bytes(b"fake")
    ori = models_dir / "challengers" / "oriserve-apex-ct2-int8"
    ori.mkdir(parents=True, exist_ok=True)
    (ori / "model.bin").write_bytes(b"fake")
    (ori / "config.json").write_text('{"model_type":"whisper"}', encoding="utf-8")


def _make_ctrl(tmp_path: Path, **overrides) -> AppController:
    storage_dir = overrides.pop("storage_dir", str(tmp_path / "data"))
    Path(storage_dir).mkdir(parents=True, exist_ok=True)
    models_dir = overrides.pop("models_dir", str(tmp_path / "models"))
    cfg = AppConfig(
        storage_dir=str(storage_dir),
        models_dir=str(models_dir),
        auto_record_calls=False,
        record_meeting_screen=False,
        **overrides,
    )
    cfg.disable_model_preload = True
    cfg.disable_hotkeys = True
    return AppController(config=cfg)


# ---------------------------------------------------------------------------
# 1. Direct Installer Handoff & Abort-Quit on Spawn Failure
# ---------------------------------------------------------------------------
def test_01_direct_updater_handoff_no_powershell_and_abort_on_popen_error(tmp_path: Path, qapp: QApplication) -> None:
    ctrl = _make_ctrl(tmp_path, theme="light")
    try:
        win = MainWindow(ctrl)
        fake_exe = tmp_path / "VoxLocal-Setup-1.0.13.exe"
        fake_exe.write_bytes(b"MZ" + b"\x00" * 64)

        with patch("subprocess.Popen") as popen_mock, patch("PyQt6.QtCore.QTimer.singleShot") as timer_mock:
            win._on_update_ready_to_install(str(fake_exe), "1.0.13")
            assert popen_mock.called is True
            cmd = popen_mock.call_args[0][0]
            assert isinstance(cmd, list)
            assert "powershell" not in str(cmd[0]).lower()
            assert cmd[0] == str(fake_exe)
            assert "--silent" in cmd
            assert "--wait-pid" in cmd
            assert "--install-dir" in cmd
            assert "--relaunch" in cmd
            assert "--expected-version" in cmd and "1.0.13" in cmd
            assert "--from-version" in cmd and voxlocal.__version__ in cmd
            flags = int(popen_mock.call_args[1].get("creationflags", 0))
            assert (flags & getattr(subprocess, "DETACHED_PROCESS", 0x00000008)) == 0
            assert timer_mock.called is True

        with patch("subprocess.Popen", side_effect=OSError("spawn blocked")), patch("PyQt6.QtCore.QTimer.singleShot") as timer_mock:
            win._update_in_progress = True
            win._on_update_ready_to_install(str(fake_exe), "1.0.13")
            assert timer_mock.called is False
            assert win._update_in_progress is False
            assert "failed" in win.lbl_update_status.text().lower()
        win.close()
    finally:
        ctrl.shutdown()


# ---------------------------------------------------------------------------
# 2. Installer CLI Space-Split --install-dir Recovery
# ---------------------------------------------------------------------------
def test_02_installer_parse_cli_args_recovers_unquoted_spaces() -> None:
    opts = installer_mod.parse_cli_args(
        [
            "--silent",
            "--wait-pid",
            "4321",
            "--install-dir",
            "D:\\Milind\\Voice",
            "Assistant\\dist\\VoxLocal",
            "--relaunch",
            "--expected-version",
            "1.0.13",
            "--from-version",
            "1.0.11",
        ]
    )
    assert opts["silent"] is True
    assert opts["wait_pid"] == 4321
    assert opts["relaunch"] is True
    assert opts["no_launch"] is False
    assert opts["expected_version"] == "1.0.13"
    assert opts["from_version"] == "1.0.11"
    assert opts["install_dir_override"] == Path("D:\\Milind\\Voice Assistant\\dist\\VoxLocal")


# ---------------------------------------------------------------------------
# 3. Atomic Backup & Rollback on Pre-Launch Verification Failure
# ---------------------------------------------------------------------------
def test_03_installer_atomic_backup_and_rollback_on_verify_failure(tmp_path: Path) -> None:
    install_dir = tmp_path / "Vox Target"
    (install_dir / "_internal").mkdir(parents=True)
    (install_dir / "src" / "voxlocal").mkdir(parents=True)
    (install_dir / "assets").mkdir(parents=True)
    (install_dir / "data").mkdir(parents=True)
    (install_dir / "VoxLocal.exe").write_bytes(b"OLD_EXE_BYTES")
    (install_dir / "src" / "voxlocal" / "__init__.py").write_text('__version__ = "1.0.11"\n', encoding="utf-8")
    (install_dir / "_internal" / "base.dll").write_bytes(b"OLD_DLL")
    (install_dir / "data" / "voxlocal.db").write_bytes(b"USER_DB_PROTECTED")

    fake_bundle = tmp_path / "bundle_root"
    (fake_bundle / "src" / "voxlocal").mkdir(parents=True)
    (fake_bundle / "bundled_bin").mkdir(parents=True)
    (fake_bundle / "bundled_internal" / "sherpa_onnx").mkdir(parents=True)
    (fake_bundle / "src" / "voxlocal" / "__init__.py").write_text('__version__ = "1.0.13"\n', encoding="utf-8")
    (fake_bundle / "bundled_bin" / "VoxLocal.exe").write_bytes(b"NEW_BAD_EXE")
    (fake_bundle / "bundled_internal" / "sherpa_onnx" / "new.pyd").write_bytes(b"NEW_PYD")

    with patch.object(installer_mod, "sys", SimpleNamespace(**{**vars(sys), "_MEIPASS": str(fake_bundle)})), \
         patch.object(installer_mod, "_stop_target_vox_processes"), \
         patch.object(installer_mod, "_verify_installed_exe", return_value=(False, "rc=2 version_mismatch")):
        rc = installer_mod.run_install(
            silent=True,
            install_dir_override=install_dir,
            no_launch=True,
            expected_version="1.0.13",
            from_version="1.0.11",
        )

    assert rc == 2
    assert (install_dir / "VoxLocal.exe").read_bytes() == b"OLD_EXE_BYTES"
    assert '__version__ = "1.0.11"' in (install_dir / "src" / "voxlocal" / "__init__.py").read_text(encoding="utf-8")
    assert not (install_dir / "_internal" / "sherpa_onnx" / "new.pyd").exists()
    assert (install_dir / "data" / "voxlocal.db").read_bytes() == b"USER_DB_PROTECTED"
    status_data = json.loads((install_dir / "data" / "logs" / "update_status.json").read_text(encoding="utf-8"))
    assert status_data["status"] == "rolled_back"


# ---------------------------------------------------------------------------
# 4. Runtime Self-Check CLI (--verify-runtime)
# ---------------------------------------------------------------------------
def test_04_main_verify_runtime_cli_pass_and_mismatch() -> None:
    res_ok = subprocess.run(
        [sys.executable, str(ROOT_DIR / "main.py"), "--verify-runtime", "--expected-version", voxlocal.__version__],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert res_ok.returncode == 0, res_ok.stderr
    jstart = res_ok.stdout.find("{")
    assert jstart >= 0, res_ok.stdout
    payload = json.loads(res_ok.stdout[jstart:])
    assert payload["ok"] is True
    assert payload["version"] == voxlocal.__version__

    res_bad = subprocess.run(
        [sys.executable, str(ROOT_DIR / "main.py"), "--verify-runtime", "--expected-version", "99.99.99"],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert res_bad.returncode == 2


# ---------------------------------------------------------------------------
# 5. Startup update_status.json Handshake (Success + Rolled Back)
# ---------------------------------------------------------------------------
def test_05_startup_update_status_handshake_transitions(tmp_path: Path, qapp: QApplication) -> None:
    ctrl = _make_ctrl(tmp_path, theme="dark")
    try:
        win = MainWindow(ctrl)
        logs_dir = Path(ctrl.config.storage_dir) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        status_p = logs_dir / "update_status.json"
        bk_dir = Path(ctrl.config.storage_dir).parent / ".update_backup"
        bk_dir.mkdir(parents=True, exist_ok=True)
        (bk_dir / "old.txt").write_text("backup", encoding="utf-8")

        status_p.write_text(
            json.dumps({"status": "pending_startup_verify", "from_version": "1.0.11", "to_version": voxlocal.__version__}),
            encoding="utf-8",
        )
        with patch.object(win, "show_toast") as toast_mock:
            win._check_post_update_status()
            assert toast_mock.called is True
            assert "Complete" in toast_mock.call_args[0][0]
        updated = json.loads(status_p.read_text(encoding="utf-8"))
        assert updated["status"] == "verified_success"
        assert not bk_dir.exists()

        status_p.write_text(
            json.dumps({"status": "rolled_back", "from_version": "1.0.11", "to_version": "1.0.13", "error": "verify failed"}),
            encoding="utf-8",
        )
        with patch.object(win, "show_toast") as toast_mock:
            win._check_post_update_status()
            assert toast_mock.called is True
            assert "Rolled Back" in toast_mock.call_args[0][0]
        rb = json.loads(status_p.read_text(encoding="utf-8"))
        assert rb["status"] == "rolled_back_notified"
        win.close()
    finally:
        ctrl.shutdown()


# ---------------------------------------------------------------------------
# 6. Themed QMessageBox Styling (Light & Dark Modes)
# ---------------------------------------------------------------------------
def test_06_themed_message_box_light_and_dark(tmp_path: Path, qapp: QApplication) -> None:
    from voxlocal.ui.theme import get_theme_tokens

    ctrl = _make_ctrl(tmp_path, theme="light")
    try:
        t_light = get_theme_tokens("light")
        t_dark = get_theme_tokens("dark")
        win = MainWindow(ctrl)
        box_l = QMessageBox(win)
        win._style_message_box(box_l)
        ss_l = box_l.styleSheet()
        assert t_light["bg"] in ss_l
        assert t_light["text"] in ss_l
        assert t_light["primary_btn_bg"] in ss_l

        win._theme = "dark"
        box_d = QMessageBox(win)
        win._style_message_box(box_d)
        ss_d = box_d.styleSheet()
        assert t_dark["bg"] in ss_d
        assert t_dark["text"] in ss_d
        assert t_dark["primary_btn_bg"] in ss_d
        win.close()
    finally:
        ctrl.shutdown()


# ---------------------------------------------------------------------------
# 7. Non-Destructive DB Migration on Copy of Real Production DB
# ---------------------------------------------------------------------------
def test_07_non_destructive_db_migration_on_real_db_copy(tmp_path: Path) -> None:
    real_db = ROOT_DIR / "data" / "voxlocal.db"
    if not real_db.exists():
        pytest.skip("Real data/voxlocal.db not present in test environment")

    copy_db = tmp_path / "voxlocal_copy.db"
    shutil.copy2(real_db, copy_db)
    with sqlite3.connect(str(copy_db)) as conn:
        orig_meetings = int(conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0])
        orig_segments = int(conn.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0])

    db = LocalDatabase(copy_db)
    try:
        with sqlite3.connect(str(copy_db)) as conn:
            new_meetings = int(conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0])
            new_segments = int(conn.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0])
            cols = {r[1] for r in conn.execute("PRAGMA table_info(meetings)").fetchall()}
        assert new_meetings == orig_meetings
        assert new_segments == orig_segments
        for col in (
            "provisional_transcript",
            "provisional_transcript_json",
            "post_call_stage",
            "post_call_status_message",
            "post_call_engine_summary",
        ):
            assert col in cols
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 8 & 9. Immediate Audio Persistence, Playback & Provisional Archive Toggle
# ---------------------------------------------------------------------------
def test_08_09_immediate_audio_save_playback_and_provisional_toggle(tmp_path: Path, qapp: QApplication) -> None:
    ctrl = _make_ctrl(tmp_path, theme="light")
    try:
        wav_m = tmp_path / "data" / "meetings" / "m1" / "mic_track.wav"
        wav_s = tmp_path / "data" / "meetings" / "m1" / "system_track.wav"
        wav_x = tmp_path / "data" / "meetings" / "m1" / "meeting_mixed.wav"
        t = np.linspace(0, 1.5, 24000, dtype=np.float32)
        _write_wav(wav_m, 0.05 * np.sin(2.0 * np.pi * 220.0 * t))
        _write_wav(wav_s, 0.05 * np.sin(2.0 * np.pi * 330.0 * t))
        _write_wav(wav_x, 0.05 * np.sin(2.0 * np.pi * 260.0 * t))

        mid = ctrl.db.create_meeting(title="Playback During Processing", model_used="qwen3-asr")
        ctrl.db.add_transcript_segment(
            meeting_id=mid,
            segment_index=1,
            start_time=0.0,
            end_time=1.4,
            speaker="Speaker 1",
            text="provisional live text",
            raw_text="provisional live text",
            source_track="system",
            transcript_stage="stable",
            transcript_state="stable",
        )
        # Persist stopped state with recording_saved & queued post-call stage
        ctrl.db.update_meeting(
            mid,
            status="transcribing",
            audio_path=str(wav_x),
            mic_audio_path=str(wav_m),
            sys_audio_path=str(wav_s),
            post_call_stage="recording_saved",
            post_call_status_message="Recording saved — queued for post-call transcription",
        )
        page = NotetakerPage(ctrl)
        page.select_meeting(mid)
        assert page.btn_play_pause.isEnabled() is True
        assert page.media_slider.isEnabled() is True
        assert "Recording Saved" in page.lbl_sel_meta.text()

        # Archive provisional segments (idempotent) + replace with final segments
        archived_1 = ctrl.db.archive_provisional_transcript(mid)
        archived_2 = ctrl.db.archive_provisional_transcript(mid)
        assert archived_1 == 1
        assert archived_2 == 0
        ctrl.db.replace_meeting_segments(
            mid,
            [
                {
                    "start_time": 0.0,
                    "end_time": 1.4,
                    "speaker": "Speaker 1",
                    "source_track": "system",
                    "raw_text": "final raw text",
                    "normalized_text": "final text",
                    "text": "Final verified text here.",
                    "transcript_stage": "final",
                    "transcript_state": "final",
                }
            ],
        )
        ctrl.db.update_meeting(
            mid,
            status="completed",
            post_call_stage="completed",
            post_call_engine_summary="qwen3-asr-0.6b-onnx-int8 (CPU INT8)",
        )

        tab = VoxInteractiveTranscriptTab(db=ctrl.db, theme="light")
        tab.load_meeting(mid)
        assert not tab.btn_toggle_provisional.isHidden()
        assert "Final Verified Transcript" in tab.lbl_transcript_provenance.text()
        assert "Final verified text here." in tab._segments[0]["text"]

        tab.btn_toggle_provisional.click()
        assert "Provisional Live Preview" in tab.lbl_transcript_provenance.text()
        assert tab._segments[0]["text"] == "provisional live text"
        assert tab.btn_toggle_provisional.text() == "Back to Final Transcript"

        tab.btn_toggle_provisional.click()
        assert "Final Verified Transcript" in tab.lbl_transcript_provenance.text()
        assert "Final verified text here." in tab._segments[0]["text"]
        page.close()
    finally:
        ctrl.shutdown()


# ---------------------------------------------------------------------------
# 10. Post-Call CPU Oriserve Verifier Fallback
# ---------------------------------------------------------------------------
def test_10_post_call_cpu_oriserve_verifier_fallback(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    _make_fake_models(models_dir)
    cfg = AppConfig(
        storage_dir=str(tmp_path / "data"),
        models_dir=str(models_dir),
        enable_phase2_asr_pipeline=True,
        asr_session_mode="auto",
        post_call_allow_cpu_verifier=True,
    )
    cpu_prof = SimpleNamespace(
        os_name="Windows 11", os_version="10.0", os_build="22631", is_64bit=True,
        cpu_name="Office CPU", cpu_cores=4, cpu_threads=8, ram_total_gb=16.0, ram_avail_gb=8.0,
        gpu_name="Intel Iris Xe", gpu_type="CPU Only", gpu_vram_gb=0.0,
        cuda_available=False, cuda_device_count=0, disk_free_gb=50.0,
        corporate_tier="Tier 2", adaptive_cpu_threads=4, recommended_whisper_model="small",
        is_minimum_met=True, is_recommended_met=True,
    )
    with patch.object(HardwareProfiler, "get_profile", return_value=cpu_prof), \
         patch.object(HardwareProfiler, "_query_live_gpu_vram_mb", return_value=(None, None)), \
         patch.object(HardwareProfiler, "_check_hf_asr_runtime_available", return_value=False):
        ep = HardwareProfiler.select_execution_profile(config=cfg)
        assert ep.selective_verifier_backend == "disabled"
        assert ep.post_call_verifier_backend == "oriserve_apex_ct2"

    fake_whisper_mod = SimpleNamespace(WhisperModel=MagicMock(return_value=MagicMock()))
    eng = OriserveApexVerifierEngine(models_dir=models_dir, allow_cpu=True, cpu_threads=4)
    with patch.object(onnx_mod, "is_cuda_runtime_available", return_value=False), \
         patch.dict(sys.modules, {"faster_whisper": fake_whisper_mod}):
        ok, msg = eng.ensure_loaded()
        assert ok is True
        assert eng.asr_device == "cpu"
        assert "CPU" in msg
        assert fake_whisper_mod.WhisperModel.call_args[1]["device"] == "cpu"


# ---------------------------------------------------------------------------
# 11. Adaptive English Parakeet Post-Call Upgrade Guard
# ---------------------------------------------------------------------------
def test_11_adaptive_english_parakeet_upgrade_and_guards(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    _make_fake_models(models_dir)
    t = np.linspace(0, 1.5, 24000, dtype=np.float32)
    wav_x = tmp_path / "mixed.wav"
    _write_wav(wav_x, 0.05 * np.sin(2.0 * np.pi * 220.0 * t))

    ctrl = _make_ctrl(tmp_path, models_dir=str(models_dir), auto_summarize_meeting=False)
    try:
        from voxlocal.audio.meeting_recorder import MeetingRecordingResult

        mid = ctrl.db.create_meeting(title="EN Upgrade", model_used="qwen3")
        ctrl.config.enable_phase2_asr_pipeline = True
        ctrl.config.post_call_adaptive_english_parakeet = True
        ctrl.config.post_call_allow_cpu_verifier = False

        ep_auto = ExecutionProfile(
            tier_id="t2", tier_label="T2", live_backend="qwen3_onnx", live_model="q", live_device="cpu", live_compute_type="int8",
            offline_backend="qwen3_onnx", offline_model="qwen3-asr-0.6b-onnx-int8", offline_device="cpu", offline_compute_type="int8",
            safe_cpu_threads=4, live_best_effort=True, max_concurrent_models=1, hf_runtime_available=False, free_vram_mb=None,
            session_mode="auto", phase2_enabled=True, available_phase2_models={"qwen3_onnx": True, "parakeet": True},
        )
        q3_segs = [
            {"start_time": 0.0, "end_time": 1.4, "speaker": "Speaker 1", "source_track": "mixed", "raw_text": "please deploy the build now", "text": "please deploy the build now", "detected_language": "en"}
        ]
        pk_segs_good = [
            {"start_time": 0.0, "end_time": 1.4, "speaker": "Speaker 1", "source_track": "mixed", "raw_text": "please deploy the production build now", "text": "please deploy the production build now", "detected_language": "en"}
        ]

        res_obj = MeetingRecordingResult(
            duration_sec=1.5,
            mixed_wav_path=str(wav_x),
            mic_wav_path="",
            sys_wav_path="",
            mic_device_name="TestMic",
            loopback_device_name="TestLoop",
            capture_mode="both",
        )

        class _FakeWhisper(LocalWhisperTranscriber):
            pass

        ctrl.transcriber = _FakeWhisper(models_dir=models_dir, model_name="small", compute_device="cpu")
        with patch.object(ctrl, "_resolve_asr_execution_profile", return_value=ep_auto), \
             patch.object(GuardedQwen3OnnxEngine, "transcribe_meeting_dual_track", return_value=q3_segs), \
             patch.object(GuardedQwen3OnnxEngine, "unload_model"), \
             patch.object(ParakeetOnnxEngine, "transcribe_meeting_dual_track", return_value=pk_segs_good) as pk_spy, \
             patch.object(ParakeetOnnxEngine, "unload_model"), \
             patch.object(ctrl.transcriber, "reconcile_meeting_transcript", side_effect=lambda **kw: kw["segments"]):
            ctrl._finalize_meeting_worker(mid, res_obj, "")
            assert pk_spy.called is True
            mtg = ctrl.db.get_meeting(mid)
            assert "parakeet" in str(mtg["model_used"]).lower()
            segs_out = ctrl.db.get_meeting_segments(mid)
            assert _polish_final_punctuation("hello world from vox") == "Hello world from vox."
            assert segs_out[0]["text"].endswith(".")

        # Hinglish utterances MUST skip Parakeet upgrade
        mid_hi = ctrl.db.create_meeting(title="Hinglish Skip", model_used="qwen3")
        hi_segs = [
            {"start_time": 0.0, "end_time": 1.4, "speaker": "Speaker 1", "source_track": "mixed", "raw_text": "abhi woh issue check karna hai kyunki nahi chal raha", "text": "abhi woh issue check karna hai kyunki nahi chal raha", "detected_language": "hi"}
        ]
        with patch.object(ctrl, "_resolve_asr_execution_profile", return_value=ep_auto), \
             patch.object(GuardedQwen3OnnxEngine, "transcribe_meeting_dual_track", return_value=hi_segs), \
             patch.object(GuardedQwen3OnnxEngine, "unload_model"), \
             patch.object(ParakeetOnnxEngine, "transcribe_meeting_dual_track") as pk_spy_hi, \
             patch.object(ctrl.transcriber, "reconcile_meeting_transcript", side_effect=lambda **kw: kw["segments"]):
            ctrl._finalize_meeting_worker(mid_hi, res_obj, "")
            assert pk_spy_hi.called is False
    finally:
        ctrl.shutdown()


# ---------------------------------------------------------------------------
# 12. Live Preview Bounded Backlog & Startup Failure Isolation
# ---------------------------------------------------------------------------
def test_12_live_preview_backlog_drop_and_startup_failure_isolation(tmp_path: Path) -> None:
    ctrl = _make_ctrl(tmp_path, live_preview_enabled=True, progressive_transcription=True, live_preview_max_backlog_sec=8.0)
    try:
        mock_eng = MagicMock()
        mock_eng.is_loaded = True
        rec = SimpleNamespace(
            elapsed_seconds=20.0,
            get_audio_window=lambda a, b: (np.zeros(8000, dtype=np.float32), np.zeros(8000, dtype=np.float32), float(a)),
        )
        st = LiveMeetingStreamingTranscriber(transcriber=ctrl.transcriber, db=ctrl.db, config=ctrl.config)
        st._active = True
        st._recorder = rec
        st._live_backend = "qwen3_onnx"
        st._phase2_engine = mock_eng
        st._track_states["system"]["cursor_sec"] = 0.0
        st._track_states["mic"]["cursor_sec"] = 0.0
        st._step_phase2_endpointed()
        tel = st.get_stream_telemetry()
        assert tel["dropped_backlog_windows"] >= 2
        assert st._track_states["system"]["cursor_sec"] >= 18.0
        assert mock_eng.transcribe_array.called is False

        # Live preview exception on start must NEVER abort audio recording!
        ctrl.meeting_recorder = MagicMock()
        ctrl.meeting_recorder.start.return_value = (True, "OK")
        ctrl.streaming_transcriber = MagicMock()
        ctrl.streaming_transcriber.start.side_effect = RuntimeError("ONNX live preview broke")
        started = ctrl.start_meeting_recording(title="Isolated Live Error")
        assert bool(started[0] if isinstance(started, tuple) else started) is True
    finally:
        ctrl.shutdown()


# ---------------------------------------------------------------------------
# 13. Offline Model Downloader Disk-Space Guard & Validation
# ---------------------------------------------------------------------------
def test_13_download_phase2_model_disk_guard_and_validation(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    with patch("shutil.disk_usage", return_value=SimpleNamespace(total=10**10, used=9 * 10**9, free=500 * 1024 * 1024)):
        ok, msg = download_phase2_model("parakeet", models_dir=models_dir, min_free_disk_mb=2500)
        assert ok is False
        assert "disk space" in msg.lower()

    fake_ok_root = tmp_path / "dl_ok"
    def _fake_snapshot(repo_id: str, local_dir: str, **kwargs) -> str:
        lp = Path(local_dir)
        lp.mkdir(parents=True, exist_ok=True)
        for rel in ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"):
            (lp / rel).write_bytes(b"ok")
        return local_dir

    with patch("shutil.disk_usage", return_value=SimpleNamespace(total=50 * 10**9, used=10 * 10**9, free=40 * 10**9)), \
         patch("huggingface_hub.snapshot_download", side_effect=_fake_snapshot):
        ok2, msg2 = download_phase2_model("parakeet", models_dir=fake_ok_root, min_free_disk_mb=2500)
        assert ok2 is True
        assert "verified" in msg2.lower()

    # Crash recovery deduplication & inflight job reset
    db = LocalDatabase(tmp_path / "recovery_dedup.db")
    try:
        mid = db.create_meeting(title="Recovery Dedup", model_used="qwen3")
        seg1 = db.add_transcript_segment(
            meeting_id=mid, start_time=1.0, end_time=3.0, speaker="Mic", text="Action item verified.", raw_text="raw action", source_track="mic"
        )
        seg2 = db.add_transcript_segment(
            meeting_id=mid, start_time=1.1, end_time=3.1, speaker="Mic", text="action item verified", raw_text="dup action", source_track="mic"
        )
        assert seg1 == seg2
        db.enqueue_finalization_job(mid)
        db.update_finalization_job(mid, state="FINALIZING", last_stage="transcribing")
        reset_cnt = db.reset_inflight_finalization_jobs_for_recovery()
        assert reset_cnt == 1
        assert db.get_finalization_job(mid)["state"] == "FINALIZATION_QUEUED"
    finally:
        db.close()

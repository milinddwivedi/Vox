"""
VOX v2.1 Comprehensive Packaging, Monorepo Safety, Model Provisioning,
Hardware Adaptability, Website Auth Gate, In-App Update & Setup Wizard Test Suite.
"""

from __future__ import annotations

import functools
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
PKG_DIR = ROOT_DIR / "packaging"
for _p in (str(SRC_DIR), str(PKG_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import voxlocal
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.core.first_run_health import run_first_run_health_check
from voxlocal.core.hardware import HardwareProfiler
from voxlocal.core.updater import UpdateManager
from voxlocal.engine.model_manifest import (
    check_manifest_compatibility,
    get_model_entries,
    get_required_model_ids,
    load_model_manifest,
)
from voxlocal.engine.model_provisioner import ModelProvisioner
from voxlocal.ui.main_window import MainWindow
import installer_wizard as wiz_mod
import vox_setup_installer as installer_mod


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


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
# 1. Monorepo Safety, Secrets Hygiene & Licensing
# ---------------------------------------------------------------------------
def test_01_monorepo_boundaries_no_secrets_no_dev_paths() -> None:
    # Website pages intact
    for page in ("index.html", "download.html", "pricing.html", "account.html", "login.html"):
        assert (ROOT_DIR / "docs" / page).is_file(), f"Missing website page docs/{page}"

    # No hardcoded private keys in API, docs, or desktop src
    for scan_dir in (ROOT_DIR / "api", ROOT_DIR / "docs", ROOT_DIR / "src"):
        for fp in scan_dir.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in (".js", ".py", ".html"):
                txt = fp.read_text(encoding="utf-8", errors="ignore")
                assert "5c5cd3cdfe894c8ed8c42afe70cc6e5ffb1e056f4262275bdb266eeb2ba315a4" not in txt, (
                    f"Hardcoded fallback signing key leaked in {fp}"
                )
                assert "-----BEGIN PRIVATE KEY-----" not in txt, f"PEM private key leaked in {fp}"

    # No hardcoded developer path in transcriber/src
    tr_txt = (ROOT_DIR / "src" / "voxlocal" / "engine" / "transcriber.py").read_text(encoding="utf-8")
    assert r"D:\Milind\Voice Assistant\data\models" not in tr_txt

    # Third-party licensing and attribution complete
    notices = ROOT_DIR / "licenses" / "THIRD_PARTY_NOTICES.md"
    assert notices.is_file()
    nt_txt = notices.read_text(encoding="utf-8")
    for token in ("Cohere", "Qwen3.5-4B", "BGE-Small-EN", "PyAnnote", "WeSpeaker", "Faster-Whisper", "llama.cpp", "PyQt6"):
        assert token in nt_txt

    # Vercel ignore excludes desktop runtime & secrets
    vi = (ROOT_DIR / ".vercelignore").read_text(encoding="utf-8")
    for excluded in ("src", "runtime", "licenses", "release_artifacts", "docs/downloads/"):
        assert excluded in vi


# ---------------------------------------------------------------------------
# 2. Canonical Model Manifest & Resilient Model Provisioning
# ---------------------------------------------------------------------------
def test_02_canonical_model_manifest_schema_and_compatibility() -> None:
    manifest = load_model_manifest()
    assert manifest["manifest_version"] == "2.1.0"
    assert check_manifest_compatibility(voxlocal.__version__, manifest) is True
    assert check_manifest_compatibility("1.0.0", manifest) is False

    entries = get_model_entries()
    for mid in ("meeting_asr", "meeting_diarization", "meeting_llm", "meeting_embeddings", "dictation_whisper_turbo", "dictation_whisper_small"):
        assert mid in entries
        spec = entries[mid]
        assert spec["license"]
        assert spec["runtime"]
        assert spec["files"]
        for fspec in spec["files"]:
            assert len(fspec["sha256"]) == 64
            assert int(fspec["size"]) > 0
            assert str(fspec["url"]).startswith("https://")

    req_16gb = get_required_model_ids(ram_total_gb=16.0)
    req_8gb = get_required_model_ids(ram_total_gb=8.0)
    assert "dictation_whisper_turbo" in req_16gb
    assert "dictation_whisper_small" not in req_16gb
    assert "dictation_whisper_small" in req_8gb
    assert "dictation_whisper_turbo" not in req_8gb

    # Every HuggingFace resolve URL must be pinned to an immutable 40-char commit SHA (never mutable /resolve/main/)
    for mid, spec in entries.items():
        for fspec in spec["files"]:
            url = str(fspec["url"])
            if "huggingface.co/" in url:
                assert "/resolve/main/" not in url, f"Unpinned mutable /resolve/main/ URL in {mid}: {url}"


def test_03_model_provisioner_resume_sha256_corrupt_rejection_and_reuse(tmp_path: Path) -> None:
    payload = b"VOX-MODEL-PAYLOAD-" * 4096
    sha = hashlib.sha256(payload).hexdigest()

    class _RangeHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            rng = self.headers.get("Range", "")
            if rng.startswith("bytes="):
                start = int(rng.split("=", 1)[1].split("-", 1)[0])
                chunk = payload[start:]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(payload)-1}/{len(payload)}")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)
            else:
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    port = srv.server_port
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        manifest_file = tmp_path / "test_manifest.json"
        manifest_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "manifest_version": "2.1.0",
                    "min_app_version": "2.0.0",
                    "models": [
                        {
                            "id": "tiny_test_model",
                            "display_name": "Tiny Test Model",
                            "revision": "rev1",
                            "version": "1.0",
                            "license": "MIT",
                            "runtime": "onnx",
                            "feature": "test",
                            "required": True,
                            "install_rel_dir": "tiny_test",
                            "expected_size_bytes": len(payload),
                            "files": [
                                {
                                    "rel_path": "weights.bin",
                                    "size": len(payload),
                                    "sha256": sha,
                                    "url": f"http://127.0.0.1:{port}/weights.bin",
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        load_model_manifest.cache_clear()
        models_dir = tmp_path / "models_a"
        prov = ModelProvisioner(models_dir=models_dir, manifest_path=str(manifest_file))

        # Pre-seed partial file to test HTTP Range resume
        part_p = prov.get_model_dir("tiny_test_model") / "weights.bin.part"
        part_p.parent.mkdir(parents=True, exist_ok=True)
        part_p.write_bytes(payload[:1024])

        ok, msg = prov.provision_model("tiny_test_model")
        assert ok is True, msg
        dest_p = prov.get_model_dir("tiny_test_model") / "weights.bin"
        assert dest_p.read_bytes() == payload

        # Corrupt destination & verify rejection
        dest_p.write_bytes(b"corrupt")
        st_bad = prov.inspect_model("tiny_test_model", verify_hash=True)
        assert st_bad["installed"] is False
        assert st_bad["corrupt_files"]

        # Re-restore valid file and test reuse into secondary dir
        dest_p.write_bytes(payload)
        models_dir_b = tmp_path / "models_b"
        prov_b = ModelProvisioner(models_dir=models_dir_b, manifest_path=str(manifest_file))
        with patch("voxlocal.engine.model_provisioner._discover_candidate_model_roots", return_value=[models_dir_b, models_dir]):
            reused, reuse_msg = prov_b.try_reuse_existing_model("tiny_test_model", verify_hash=True)
            assert reused is True, reuse_msg
            assert (prov_b.get_model_dir("tiny_test_model") / "weights.bin").read_bytes() == payload
    finally:
        load_model_manifest.cache_clear()
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------------------
# 3. First-Run Health Check & Hardware Adaptability
# ---------------------------------------------------------------------------
def test_04_first_run_health_check_truthfulness_and_hardware_fallback(tmp_path: Path) -> None:
    empty_models = tmp_path / "empty_models"
    empty_models.mkdir(parents=True, exist_ok=True)
    cfg = AppConfig(storage_dir=str(tmp_path / "data"), models_dir=str(empty_models))

    # Avoid falling back to user's real installed models during empty-model check
    import gc

    gc.collect()
    with patch("voxlocal.engine.transcriber.LocalWhisperTranscriber.resolve_model_path", return_value=("id", str(empty_models), False)):
        report = run_first_run_health_check(config=cfg, verify_hashes=False)

    assert report["runtime_ok"] is True, f"modules={report.get('modules')} missing={report.get('missing_components')}"
    assert report["features"]["vox_installed"] is True
    assert report["overall_ready"] is False
    assert report["features"]["meeting_transcription_ready"] is False
    assert "meeting_asr" in report["missing_components"]

    # CPU-only & Low VRAM hardware diagnostics never load heavy models
    cpu_prof = SimpleNamespace(
        os_name="Windows 11", os_version="10.0", os_build="26100", is_64bit=True,
        cpu_name="Intel Core i5", cpu_cores=4, cpu_threads=8, ram_total_gb=8.0, ram_avail_gb=4.0,
        gpu_name="Intel Iris Xe", gpu_type="CPU Only", gpu_vram_gb=0.0,
        cuda_available=False, cuda_device_count=0, disk_free_gb=40.0,
        corporate_tier="Tier 1", adaptive_cpu_threads=4, recommended_whisper_model="small",
        is_minimum_met=True, is_recommended_met=False,
    )
    with patch.object(HardwareProfiler, "get_profile", return_value=cpu_prof), \
         patch.object(HardwareProfiler, "_query_live_gpu_vram_mb", return_value=(None, None)), \
         patch.object(HardwareProfiler, "_check_hf_asr_runtime_available", return_value=False):
        ep = HardwareProfiler.select_execution_profile(config=cfg)
        assert ep.tier_id == "tier1_minimum"
        assert ep.offline_device == "cpu"


# ---------------------------------------------------------------------------
# 4. Account-Aware In-App Updates, Deferral & Auth Enforcement
# ---------------------------------------------------------------------------
def test_05_inapp_update_defers_recording_and_finalization_and_enforces_auth(tmp_path: Path, qapp: QApplication) -> None:
    ctrl = _make_ctrl(tmp_path, theme="light")
    try:
        win = MainWindow(ctrl)
        manifest = {
            "version": "2.1.0",
            "installer_url": "https://vox-voice-assistant.vercel.app/api/download/file",
            "installer_size_bytes": 120_000_000,
            "sha256": "a" * 64,
            "release_date": "2026-11-27",
        }

        # 1. Defers when finalizing post-call AI job
        ctrl._finalizing_jobs.add(999)
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok):
            win._start_in_app_update(manifest)
        assert win._update_in_progress is False
        ctrl._finalizing_jobs.clear()

        # 2. Prompts sign-in when unauthenticated for gated /api/download/ URL
        with patch.object(ctrl.desktop_auth_broker, "load_session", return_value=None), \
             patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Cancel):
            win._start_in_app_update(manifest)
        assert win._update_in_progress is False

        # 3. UpdateManager refuses silent bypass when /api/download/ lacks token
        mgr = UpdateManager(current_version="2.0.0")
        ok, dl_url, msg = mgr.resolve_download_url(manifest, auth_token=None)
        assert ok is False
        assert "sign-in" in msg.lower() or "required" in msg.lower()
        win.close()
    finally:
        ctrl.shutdown()


# ---------------------------------------------------------------------------
# 5. Setup Wizard GUI Stages, Uninstaller Preservation & Screenshots
# ---------------------------------------------------------------------------
def test_06_setup_wizard_gui_pages_and_uninstaller_preserves_data(tmp_path: Path, qapp: QApplication) -> None:
    install_dir = tmp_path / "ProgramsVOX"
    (install_dir / "data").mkdir(parents=True)
    (install_dir / "VoxLocal.exe").write_bytes(b"EXE")
    (install_dir / "data" / "voxlocal.db").write_bytes(b"KEEP_MY_MEETINGS")

    # Default uninstall preserves user data
    rc = installer_mod.run_uninstall(install_dir, purge_data=False)
    assert rc == 0
    assert not (install_dir / "VoxLocal.exe").exists()
    assert (install_dir / "data" / "voxlocal.db").read_bytes() == b"KEEP_MY_MEETINGS"

    # Setup Wizard GUI captures all 4 pages without error
    shot_dir = Path(r"C:\Users\milin\.gemini\antigravity\brain\9b0bd9e3-7696-49ec-8ae0-ed63b06e7018\ui_screenshots")
    wiz = wiz_mod.VoxSetupWizard(
        version="2.1.0",
        default_install_dir=install_dir,
        runner_fn=lambda **kw: 0,
    )
    shots = wiz.capture_verification_screenshots(shot_dir)
    assert len(shots) == 4
    for s in shots:
        assert s.is_file() and s.stat().st_size > 10_000
    assert wiz.chk_launch_now.isChecked() is True
    wiz.close()


# ---------------------------------------------------------------------------
# 6. Packaged Artifact Hygiene Inspection
# ---------------------------------------------------------------------------
def test_07_packaged_dist_and_release_artifacts_clean() -> None:
    dist_app = ROOT_DIR / "dist" / "VoxLocal"
    if not (dist_app / "VoxLocal.exe").is_file():
        pytest.skip("dist/VoxLocal not yet built")

    # Ensure no user DBs, WAV recordings, .env secrets, or website HTML leaked into app bundle
    for f in dist_app.rglob("*"):
        if not f.is_file():
            continue
        rel = str(f.relative_to(dist_app)).replace("\\", "/")
        assert not rel.startswith("data/"), f"User data dir found in dist: {rel}"
        if not rel.startswith("_internal/"):
            assert f.suffix.lower() not in (".db", ".wav", ".mp4", ".html", ".vault", ".pem", ".key"), rel
            assert f.name.lower() not in ("config.json", ".env", ".env.production.local"), rel

    assert (dist_app / "licenses" / "THIRD_PARTY_NOTICES.md").is_file() or (dist_app / "_internal" / "licenses" / "THIRD_PARTY_NOTICES.md").is_file()
    assert (dist_app / "model_manifest.json").is_file() or (dist_app / "_internal" / "model_manifest.json").is_file()
    assert (dist_app / "assets" / "fonts" / "GoogleSans.ttf").is_file() or (dist_app / "_internal" / "assets" / "fonts" / "GoogleSans.ttf").is_file()


def test_08_full_payload_upgrade_removes_and_backs_up_legacy_src(tmp_path: Path) -> None:
    import tarfile

    fake_base = tmp_path / "installer_base"
    (fake_base / "bundled_payload").mkdir(parents=True)
    (fake_base / "packaging").mkdir(parents=True)
    (fake_base / "packaging" / "version.json").write_text('{"version": "2.1.0"}', encoding="utf-8")

    stage = tmp_path / "stage_app"
    stage.mkdir(parents=True)
    (stage / "VoxLocal.exe").write_bytes(b"NEW_EXE_210")

    tar_path = fake_base / "bundled_payload" / "app_payload.tar.xz"
    with tarfile.open(tar_path, "w:xz") as tf:
        tf.add(stage / "VoxLocal.exe", arcname="VoxLocal.exe")

    install_dir = tmp_path / "legacy_install"
    (install_dir / "src" / "voxlocal").mkdir(parents=True)
    (install_dir / "src" / "voxlocal" / "__init__.py").write_text('__version__ = "2.0.0"\n', encoding="utf-8")
    (install_dir / "VoxLocal.exe").write_bytes(b"OLD_EXE_200")
    backup_dir = install_dir / ".update_backup"

    with patch.object(sys, "_MEIPASS", str(fake_base), create=True):
        res = installer_mod.install_bundled_components(install_dir, backup_dir=backup_dir)

    assert res["installed_any"] is True
    assert not (install_dir / "src").exists()
    assert (install_dir / "VoxLocal.exe").read_bytes() == b"NEW_EXE_210"
    assert (backup_dir / "src" / "voxlocal" / "__init__.py").read_text(encoding="utf-8") == '__version__ = "2.0.0"\n'

    # Rollback cleanly restores legacy src/ if verification ever fails
    assert installer_mod._restore_from_backup(install_dir, backup_dir, res["created_rel"]) is True
    assert (install_dir / "src" / "voxlocal" / "__init__.py").read_text(encoding="utf-8") == '__version__ = "2.0.0"\n'
    assert (install_dir / "VoxLocal.exe").read_bytes() == b"OLD_EXE_200"


# ---------------------------------------------------------------------------
# 7. Data Separation Migration, Adaptive Sizing & Resumable Ticket-Renewed Updates
# ---------------------------------------------------------------------------
def test_09_data_migration_separates_user_data_rewrites_paths_and_is_idempotent(tmp_path: Path) -> None:
    from voxlocal.core.data_migration import migrate_legacy_install_data

    install_dir = tmp_path / "ProgramsVOX"
    legacy_data = install_dir / "data"
    (legacy_data / "recordings" / "mtg_1").mkdir(parents=True)
    (legacy_data / "models" / "demo_model").mkdir(parents=True)
    wav_file = legacy_data / "recordings" / "mtg_1" / "sys.wav"
    wav_file.write_bytes(b"RIFF_AUDIO_BYTES")
    (legacy_data / "models" / "demo_model" / "weights.bin").write_bytes(b"MODEL_DATA")
    (legacy_data / "auth_session.vault").write_bytes(b"VAULT_TOKEN")

    legacy_cfg = {
        "storage_dir": str(legacy_data),
        "models_dir": str(legacy_data / "models"),
        "theme": "dark",
    }
    (legacy_data / "config.json").write_text(json.dumps(legacy_cfg), encoding="utf-8")

    db_p = legacy_data / "voxlocal.db"
    conn = sqlite3.connect(str(db_p))
    conn.execute("CREATE TABLE meetings (id INTEGER PRIMARY KEY, title TEXT, audio_path TEXT, sys_audio_path TEXT)")
    conn.execute(
        "INSERT INTO meetings VALUES (1, 'Board Sync', ?, ?)",
        (str(wav_file), str(wav_file)),
    )
    conn.execute("CREATE TABLE transcript_segments (id INTEGER PRIMARY KEY, meeting_id INTEGER, text TEXT)")
    conn.execute("INSERT INTO transcript_segments VALUES (1, 1, 'Migrated cleanly')")
    conn.commit()
    conn.close()

    target_root = tmp_path / "UserVOX"
    res = migrate_legacy_install_data(install_dir, target_root)
    assert res["migrated"] is True, res
    assert not legacy_data.exists(), "Legacy install data dir must be removed after verified migration"

    new_db = target_root / "data" / "voxlocal.db"
    assert new_db.is_file()
    assert (target_root / "data" / ".migration_complete.json").is_file()
    assert list((target_root / "data" / "backups").glob("voxlocal_pre_migration_*.db"))
    assert (target_root / "models" / "demo_model" / "weights.bin").read_bytes() == b"MODEL_DATA"
    assert (target_root / "data" / "recordings" / "mtg_1" / "sys.wav").read_bytes() == b"RIFF_AUDIO_BYTES"
    assert (target_root / "data" / "auth_session.vault").read_bytes() == b"VAULT_TOKEN"

    cfg_migrated = json.loads((target_root / "data" / "config.json").read_text(encoding="utf-8"))
    assert cfg_migrated["storage_dir"] == str((target_root / "data").resolve())
    assert cfg_migrated["models_dir"] == str((target_root / "models").resolve())

    c2 = sqlite3.connect(f"file:{new_db}?mode=ro", uri=True)
    assert c2.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    m_row = c2.execute("SELECT audio_path, sys_audio_path FROM meetings WHERE id=1").fetchone()
    s_cnt = c2.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0]
    c2.close()
    assert m_row[0] == str((target_root / "data" / "recordings" / "mtg_1" / "sys.wav").resolve())
    assert s_cnt == 1

    # Second run is cleanly idempotent
    res2 = migrate_legacy_install_data(install_dir, target_root)
    assert res2["migrated"] is False


def test_10_hardware_adaptive_dictation_selection_and_disk_space_guard(tmp_path: Path) -> None:
    from voxlocal.engine.model_manifest import calculate_model_selection_summary

    sum_16 = calculate_model_selection_summary(ram_total_gb=16.0)
    sum_8 = calculate_model_selection_summary(ram_total_gb=8.0)

    assert sum_16["dictation_model_id"] == "dictation_whisper_turbo"
    assert "dictation_whisper_small" not in sum_16["selected_model_ids"]
    assert sum_16["total_selected_bytes"] == 7_350_358_533
    assert sum_16["total_selected_gb_decimal"] == 7.35

    assert sum_8["dictation_model_id"] == "dictation_whisper_small"
    assert "dictation_whisper_turbo" not in sum_8["selected_model_ids"]
    assert sum_8["total_selected_bytes"] == 6_214_904_922
    assert sum_8["total_selected_gb_decimal"] == 6.21

    prov = ModelProvisioner(models_dir=tmp_path / "models")
    plan_8 = prov.estimate_provisioning_plan(ram_total_gb=8.0)
    assert plan_8["total_selected_bytes"] == 6_214_904_922
    assert plan_8["required_free_disk_bytes"] > plan_8["download_needed_bytes"]

    # Simulate low free disk space -> fast-fail BEFORE any network attempt
    low_usage = shutil._ntuple_diskusage(total=100_000_000_000, used=99_500_000_000, free=500_000_000)
    with patch("voxlocal.engine.model_provisioner._discover_candidate_model_roots", return_value=[tmp_path / "models"]), \
         patch("voxlocal.engine.model_provisioner.shutil.disk_usage", return_value=low_usage):
        ok, res = prov.provision_required_models(ram_total_gb=8.0)
    assert ok is False
    assert "_disk_check" in res
    assert "Insufficient free disk space" in res["_disk_check"]["message"]


def test_11_updater_slow_interrupted_transfer_ticket_renewal_and_range_resume(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from voxlocal.core.updater import canonicalize_manifest

    payload = (b"VOX-210-INSTALLER-BLOCK-" * 8192)  # ~196 KB
    payload_sha = hashlib.sha256(payload).hexdigest()

    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    state = {"ticket_num": 0, "file_reqs": 0, "saw_range": False}

    class _AuthResumeServer(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path == "/api/download/authorize":
                auth = self.headers.get("Authorization", "")
                if auth != "Bearer good_user_jwt":
                    self.send_response(401)
                    self.end_headers()
                    return
                state["ticket_num"] += 1
                tok = f"tkt_{state['ticket_num']}"
                body = json.dumps({
                    "ok": True,
                    "download_url": f"http://127.0.0.1:{srv.server_port}/api/download/file?ticket={tok}",
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def do_GET(self):
            state["file_reqs"] += 1
            if "ticket=tkt_1" in self.path:
                # 1st attempt: drop connection mid-stream after sending partial prefix
                if state["file_reqs"] == 1:
                    half = len(payload) // 3
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload[:half])
                    self.wfile.flush()
                    return  # Abruptly close socket -> IncompleteRead on client
                # 2nd attempt: simulate first short-lived ticket expiring -> HTTP 401 forces renewal
                self.send_response(401)
                self.end_headers()
                return

            if "ticket=tkt_2" in self.path:
                rng = self.headers.get("Range", "")
                if rng.startswith("bytes="):
                    state["saw_range"] = True
                    start = int(rng.split("=", 1)[1].split("-", 1)[0])
                    rest = payload[start:]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(payload)-1}/{len(payload)}")
                    self.send_header("Content-Length", str(len(rest)))
                    self.end_headers()
                    self.wfile.write(rest)
                    return

            self.send_response(401)
            self.end_headers()

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _AuthResumeServer)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        manifest = {
            "version": "2.1.0",
            "installer_url": f"http://127.0.0.1:{srv.server_port}/api/download/file",
            "installer_size_bytes": len(payload),
            "sha256": payload_sha,
        }
        manifest["signature"] = priv.sign(canonicalize_manifest(manifest)).hex()

        mgr = UpdateManager(current_version="2.0.0", public_key_hex=pub_hex)
        dest_exe = tmp_path / "VoxLocal-Setup.exe"
        ok, msg = mgr.download_and_verify(
            manifest,
            dest_exe,
            auth_token="good_user_jwt",
            auth_api_url=f"http://127.0.0.1:{srv.server_port}/api/download/authorize",
            max_retries=5,
        )
        assert ok is True, msg
        assert state["ticket_num"] == 2
        assert state["saw_range"] is True
        assert dest_exe.read_bytes() == payload

        # Tampered manifest SHA-256 rejection never activates binary
        bad_dest = tmp_path / "Tampered-Setup.exe"
        bad_manifest = dict(manifest, sha256="0" * 64)
        bad_manifest["signature"] = priv.sign(canonicalize_manifest(bad_manifest)).hex()
        state["ticket_num"] = 1  # Force clean ticket 2 direct download
        ok_bad, msg_bad = mgr.download_and_verify(
            bad_manifest,
            bad_dest,
            auth_token="good_user_jwt",
            auth_api_url=f"http://127.0.0.1:{srv.server_port}/api/download/authorize",
        )
        assert ok_bad is False
        assert not bad_dest.exists()
    finally:
        srv.shutdown()
        srv.server_close()


def test_12_migration_refuses_source_workspace_and_never_deletes_when_target_db_exists(tmp_path: Path) -> None:
    from voxlocal.core.data_migration import migrate_legacy_install_data

    # 1. Source workspace guard (main.py / src/voxlocal / .git present)
    ws_dir = tmp_path / "repo_ws"
    (ws_dir / "src" / "voxlocal").mkdir(parents=True)
    (ws_dir / "main.py").write_text("# dev entry\n", encoding="utf-8")
    (ws_dir / "data").mkdir(parents=True)
    canary = ws_dir / "data" / "voxlocal.db"
    canary.write_bytes(b"WORKSPACE_DB")

    target_root = tmp_path / "UserVOX"
    res_ws = migrate_legacy_install_data(ws_dir, target_root)
    assert res_ws["migrated"] is False
    assert res_ws["reason"] == "refused_source_workspace"
    assert canary.read_bytes() == b"WORKSPACE_DB"

    # 2. Target DB conflict guard -> never clobbers or deletes source legacy_data
    inst_dir = tmp_path / "ProgramsVOX"
    (inst_dir / "data").mkdir(parents=True)
    (inst_dir / "data" / "voxlocal.db").write_bytes(b"SRC_DB")
    (target_root / "data").mkdir(parents=True)
    (target_root / "data" / "voxlocal.db").write_bytes(b"EXISTING_TARGET_DB")

    res_conf = migrate_legacy_install_data(inst_dir, target_root)
    assert res_conf["migrated"] is False
    assert res_conf["reason"] == "target_db_already_exists"
    assert (inst_dir / "data" / "voxlocal.db").read_bytes() == b"SRC_DB"
    assert (target_root / "data" / "voxlocal.db").read_bytes() == b"EXISTING_TARGET_DB"

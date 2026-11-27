"""
Comprehensive Verification Suite for VOX v2.0 Post-Call English Meeting Pipeline
and Local Meeting Intelligence Redesign.
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
from unittest.mock import MagicMock

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.engine.knowledge_store import LocalKnowledgeStore, LocalOnnxEmbedder
from voxlocal.engine.llama_server_engine import LlamaServerManager
from voxlocal.engine.meeting_asr_pipeline import (
    PostCallEnglishASRPipeline,
    apply_meeting_glossary,
    detect_vad_speech_chunks,
    load_wav_mono_16k_readonly,
    suppress_mic_echo_bleed,
)
from voxlocal.engine.meeting_model_manager import MeetingModelManager
from voxlocal.storage.database import LocalDatabase
from voxlocal.storage.exporter import TranscriptExporter
from voxlocal.ui.meeting_studio import (
    VoxInteractiveTranscriptTab,
    VoxKnowledgeBaseTab,
    VoxMeetingStudioWorkspace,
    VoxSummaryDeliverablesTab,
)
from voxlocal.ui.settings_dialog import SettingsWorkspaceWidget


M247_DIR = ROOT_DIR / "data" / "recordings" / "meeting_20261125_213207"
SCRATCH_BENCH_CHUNKS = Path(
    r"C:\Users\milin\.gemini\antigravity\brain\9b0bd9e3-7696-49ec-8ae0-ed63b06e7018\scratch\benchmarks\canonical_m247_chunks.json"
)


def _active_llama_servers() -> list[str]:
    res = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
    )
    lines = [ln.strip() for ln in (res.stdout or "").splitlines() if "llama-server.exe" in ln.lower()]
    return lines


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["vox_v2_test"])
    return app


def test_v6_to_v7_migration_non_destructive_on_real_db_clone(tmp_path: Path) -> None:
    real_db = ROOT_DIR / "data" / "voxlocal.db"
    if not real_db.exists():
        pytest.skip("Real data/voxlocal.db not present in workspace")

    cloned = tmp_path / "cloned_voxlocal.db"
    shutil.copy2(real_db, cloned)

    with sqlite3.connect(str(cloned)) as conn:
        pre_ver = int(conn.execute("PRAGMA user_version").fetchone()[0])
        pre_counts = {
            tbl: int(conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0])
            for tbl in ("meetings", "transcript_segments", "dictation_history", "custom_vocabulary")
        }

    db = LocalDatabase(cloned)
    with db._connect() as conn:
        post_ver = int(conn.execute("PRAGMA user_version").fetchone()[0])
        integ = str(conn.execute("PRAGMA integrity_check").fetchone()[0]).lower()
        assert post_ver == 7
        assert integ == "ok"

        for tbl, expected in pre_counts.items():
            actual = int(conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0])
            assert actual == expected, f"Row count mismatch for {tbl}: {actual} != {expected}"

        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for req_tbl in (
            "speaker_aliases",
            "knowledge_items",
            "knowledge_sources",
            "rag_chunks",
            "rag_chunks_fts",
        ):
            assert req_tbl in tables, f"Missing migrated table: {req_tbl}"

        m_cols = {r[1] for r in conn.execute("PRAGMA table_info(meetings)").fetchall()}
        for col in (
            "detailed_summary",
            "key_points_json",
            "follow_ups_json",
            "llm_model_used",
            "llm_device_used",
            "llm_fallback_reason",
            "knowledge_indexed_at",
        ):
            assert col in m_cols, f"Missing meetings column: {col}"

    if pre_ver < 7:
        snaps = list((tmp_path / "snapshots").glob("voxlocal_pre_v7_*.db"))
        assert len(snaps) >= 1, "Expected pre-v7 snapshot backup file on v6->v7 migration"


def test_echo_bleed_suppression_real_slice_and_silence_probes() -> None:
    if not (M247_DIR / "system_track.wav").is_file():
        pytest.skip("M#247 recording not present")

    # 1. Verify read-only WAV load & synthetic/real echo-bleed suppression
    sys_audio = load_wav_mono_16k_readonly(M247_DIR / "system_track.wav")[: 16000 * 60]
    assert sys_audio.size >= 16000 * 30

    # Create mic signal: [5..9s] = attenuated bleed of sys_audio; [20..23s] = independent user voice tone/noise
    rng = np.random.default_rng(42)
    t_ax = np.arange(sys_audio.size, dtype=np.float32) / 16000.0
    mic_sim = np.zeros_like(sys_audio)
    # Attenuated delayed bleed from system track in 5s..15s
    delay = 320  # 20ms
    mic_sim[16000 * 5 + delay : 16000 * 15] = 0.25 * sys_audio[16000 * 5 : 16000 * 15 - delay]
    # Independent speech-like signal in 22s..25s where sys is silent or uncorrelated
    mic_sim[16000 * 22 : 16000 * 25] = (
        0.12 * np.sin(2.0 * np.pi * 210.0 * t_ax[16000 * 22 : 16000 * 25])
        + 0.03 * rng.normal(size=16000 * 3).astype(np.float32)
    )

    chunks = [
        {"start_sec": 5.0, "end_sec": 15.0, "start_sample": 16000 * 5, "end_sample": 16000 * 15},
        {"start_sec": 22.0, "end_sec": 25.0, "start_sample": 16000 * 22, "end_sample": 16000 * 25},
    ]
    kept, stats = suppress_mic_echo_bleed(mic_sim, sys_audio, chunks)
    assert stats["mic_chunks_suppressed_bleed"] >= 1
    assert any(abs(c["start_sec"] - 22.0) < 0.5 for c in kept)

    # 2. Verify 0/15 silence hallucinations via RMS/VAD gate across benchmark silence probes
    pipe = PostCallEnglishASRPipeline(models_dir=ROOT_DIR / "data" / "models", num_threads=4)
    if pipe.is_available() and SCRATCH_BENCH_CHUNKS.is_file():
        data = json.loads(SCRATCH_BENCH_CHUNKS.read_text(encoding="utf-8"))
        probes = data.get("silence_probes", [])
        assert len(probes) == 15
        full_sys = load_wav_mono_16k_readonly(M247_DIR / "system_track.wav")
        non_empty_silence = 0
        for p in probes:
            s0 = int(p["start_sample"])
            s1 = int(p["end_sample"])
            seg_arr = full_sys[s0:s1]
            chunks_p = detect_vad_speech_chunks(seg_arr)
            if chunks_p:
                for ch in chunks_p:
                    c0 = int(ch["start_sample"])
                    c1 = int(ch["end_sample"])
                    txt = pipe.transcribe_chunk(seg_arr[c0:c1])
                    if txt.strip():
                        non_empty_silence += 1
        assert non_empty_silence == 0


def test_raw_text_immutability_glossary_and_speaker_alias_persistence(tmp_path: Path) -> None:
    db = LocalDatabase(tmp_path / "vox_v2.db")
    vocab = [{"spoken_phrase": "coh here", "replacement": "Cohere", "category": "glossary", "is_active": 1}]
    raw, corrected = apply_meeting_glossary("We benchmarked coh here transcribe today.", vocabulary=vocab)
    assert raw == "We benchmarked coh here transcribe today."
    assert corrected == "We benchmarked Cohere transcribe today."
    assert raw != corrected

    mid = db.create_meeting(title="Architecture Sync")
    db.replace_meeting_segments(
        mid,
        [
            {
                "start_time": 1.0,
                "end_time": 4.0,
                "speaker": "Speaker 1",
                "text": corrected,
                "raw_text": raw,
                "normalized_text": raw,
                "corrected_text": corrected,
                "source_track": "system",
            }
        ],
    )
    updated = db.rename_meeting_speaker(mid, "Speaker 1", "Hemanth")
    assert updated >= 1
    aliases = db.get_speaker_aliases(mid)
    assert aliases.get("Speaker 1") == "Hemanth"
    segs = db.get_meeting_segments(mid)
    assert segs[0]["speaker"] == "Hemanth"
    assert segs[0]["raw_text"] == raw
    assert segs[0]["corrected_text"] == corrected


def test_action_item_null_owner_and_due_date_preservation(tmp_path: Path) -> None:
    db = LocalDatabase(tmp_path / "null_safety.db")
    mid = db.create_meeting(title="Null Action Test")
    aid = db.add_action_item(mid, "Verify Docker healthcheck logs", owner=None, due_date=None)
    assert aid > 0
    m = db.get_meeting(mid)
    assert m is not None
    actions = m["actions_list"]
    assert len(actions) == 1
    assert actions[0]["owner"] is None
    assert actions[0]["due_date"] is None

    exported = json.loads(TranscriptExporter.to_json(m))
    assert exported["action_items"][0]["owner"] is None
    assert exported["action_items"][0]["due_date"] is None


def test_llama_server_lifecycle_vulkan_and_cpu_fallback_zero_orphans() -> None:
    mgr_gpu = LlamaServerManager(
        model_path=ROOT_DIR / "data" / "models" / "meeting_llm" / "Qwen3.5-4B-Q4_K_M.gguf",
        device="auto",
        ctx_size=4096,
        idle_timeout_sec=60,
    )
    if not mgr_gpu.is_available():
        pytest.skip("Qwen3.5-4B GGUF or llama-server binary not installed")

    try:
        out_gpu = mgr_gpu.chat(
            [{"role": "user", "content": "Reply with one word: READY"}],
            max_tokens=16,
            temperature=0.0,
        )
        assert out_gpu is not None and len(out_gpu.strip()) > 0
        assert mgr_gpu.active_device in ("vulkan_gpu", "vulkan", "cpu")
    finally:
        mgr_gpu.stop()

    assert _active_llama_servers() == [], "Orphan llama-server.exe after GPU stop"

    mgr_cpu = LlamaServerManager(
        model_path=ROOT_DIR / "data" / "models" / "meeting_llm" / "Qwen3.5-4B-Q4_K_M.gguf",
        device="cpu",
        ctx_size=2048,
        idle_timeout_sec=60,
    )
    try:
        out_cpu = mgr_cpu.chat(
            [{"role": "user", "content": "Reply with one word: CPUOK"}],
            max_tokens=16,
            temperature=0.0,
        )
        assert out_cpu is not None and len(out_cpu.strip()) > 0
        assert mgr_cpu.active_device == "cpu"
        assert mgr_cpu.fallback_reason != ""
    finally:
        mgr_cpu.stop()

    assert _active_llama_servers() == [], "Orphan llama-server.exe after CPU fallback stop"


def test_knowledge_store_hybrid_rag_dedup_conflicts_and_user_edit_protection(tmp_path: Path) -> None:
    db = LocalDatabase(tmp_path / "rag_test.db")
    embed_dir = ROOT_DIR / "data" / "models" / "meeting_embeddings" / "bge-small-en-v1.5-onnx-Q"
    if not (embed_dir / "model_optimized.onnx").is_file():
        pytest.skip("BGE-Small-EN ONNX model not installed")

    store = LocalKnowledgeStore(db, models_dir=ROOT_DIR / "data" / "models")
    m1 = db.create_meeting(title="Q4 Release Planning")
    db.replace_meeting_segments(
        m1,
        [
            {"start_time": 10.0, "end_time": 20.0, "speaker": "Alice", "text": "We chose Vulkan llama-server with Qwen3.5-4B for local intelligence.", "source_track": "system"},
            {"start_time": 21.0, "end_time": 32.0, "speaker": "You (Mic)", "text": "Target release version is 2.0.0 with Cohere Transcribe 2B.", "source_track": "mic"},
        ],
    )
    chunks_count = store.index_meeting(m1)
    assert chunks_count >= 1

    # Dedup on identical knowledge
    r1 = db.upsert_knowledge_item(
        item_type="project",
        canonical_key="vox v2",
        title="VOX v2",
        content="Uses Cohere Transcribe 2B and Qwen3.5-4B",
        valid_from_date="2026-11-20",
        source_meeting_id=m1,
        source_meeting_title="Q4 Release Planning",
        source_meeting_date="2026-11-20",
        source_speaker="Alice",
        source_timestamp_sec=12.0,
        source_evidence_quote="Cohere Transcribe 2B",
    )
    r2 = db.upsert_knowledge_item(
        item_type="project",
        canonical_key="vox v2",
        title="VOX v2",
        content="Uses Cohere Transcribe 2B and Qwen3.5-4B",
        valid_from_date="2026-11-21",
        source_meeting_id=m1,
    )
    assert r1["id"] == r2["id"]
    assert r2["action"] in ("deduplicated", "updated")

    # Conflicting update preserves dated superseded history
    r3 = db.upsert_knowledge_item(
        item_type="project",
        canonical_key="vox v2",
        title="VOX v2",
        content="Switched target release window to December 2026",
        valid_from_date="2026-11-26",
        source_meeting_id=m1,
    )
    assert r3["action"] == "conflicted"
    assert r3["superseded_id"] == r1["id"]
    all_items = db.list_knowledge_items(include_superseded=True)
    statuses = {it["id"]: it["status"] for it in all_items}
    assert statuses[r1["id"]] == "superseded"
    assert statuses[r3["id"]] == "active"

    # user_edited=1 protection prevents overwrite
    db.update_knowledge_item(int(r3["id"]), content="User locked truth", user_edited=1)
    r4 = db.upsert_knowledge_item(
        item_type="project",
        canonical_key="vox v2",
        title="VOX v2",
        content="Attempted automatic overwrite",
        valid_from_date="2026-11-27",
    )
    assert r4["id"] == r3["id"]
    assert r4["action"] == "updated"
    locked = db.get_knowledge_item(int(r3["id"]))
    assert locked is not None and locked["content"] == "User locked truth"

    # Hybrid RRF search returns citations & knowledge
    cits, k_hits = store.hybrid_search("What LLM does VOX use with Vulkan?", top_k=5)
    assert len(cits) >= 1
    assert any("Qwen3.5-4B" in c.get("quote", "") for c in cits)
    assert len(k_hits) >= 1


def test_finalization_job_crash_recovery_queueing(tmp_path: Path) -> None:
    db = LocalDatabase(tmp_path / "recovery.db")
    wav_dir = tmp_path / "rec"
    wav_dir.mkdir(parents=True, exist_ok=True)
    sys_wav = wav_dir / "system_track.wav"
    with wave.open(str(sys_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(np.zeros(16000, dtype=np.int16).tobytes())

    mid = db.create_meeting(title="Interrupted Call")
    db.update_meeting(mid, status="interrupted", sys_audio_path=str(sys_wav), audio_path=str(sys_wav))
    db.enqueue_finalization_job(mid, sys_audio_path=str(sys_wav))
    pending = db.list_pending_finalization_jobs()
    assert any(int(j["meeting_id"]) == int(mid) for j in pending)


def test_v2_ui_widgets_smoke_raw_toggle_fallback_banner_knowledge_and_settings(tmp_path: Path, qapp: QApplication) -> None:
    db = LocalDatabase(tmp_path / "ui_smoke.db")
    mid = db.create_meeting(title="UI Verification Call")
    db.replace_meeting_segments(
        mid,
        [
            {
                "start_time": 2.0,
                "end_time": 6.0,
                "speaker": "Speaker 1",
                "text": "Cohere Transcribe works cleanly.",
                "raw_text": "coh here transcribe works cleanly.",
                "normalized_text": "coh here transcribe works cleanly.",
                "corrected_text": "Cohere Transcribe works cleanly.",
            }
        ],
    )
    db.update_meeting(
        mid,
        status="completed",
        summary="Executive summary body",
        detailed_summary="Detailed summary paragraph",
        key_points_json=json.dumps([{"topic": "ASR", "summary": "Cohere selected", "timestamp_str": "00:02"}]),
        follow_ups_json=json.dumps([{"item": "Share report", "owner": None, "timestamp_str": "00:05"}]),
        llm_fallback_reason="CPU fallback active",
        llm_model="Qwen3.5-4B-Q4_K_M.gguf",
    )
    db.upsert_knowledge_item(item_type="acronym", canonical_key="asr", title="ASR", content="Automatic Speech Recognition")

    ws = VoxMeetingStudioWorkspace(theme="dark", db=db)
    ws.load_meeting(mid)

    # 4 unified tabs including Knowledge
    assert ws.unified_tabs.count() == 4
    assert isinstance(ws.tab_knowledge, VoxKnowledgeBaseTab)
    assert ws.tab_knowledge.table.rowCount() >= 1

    # Raw vs Corrected transcript toggle
    ws.tab_transcript.btn_toggle_raw.setChecked(True)
    ws.tab_transcript._toggle_raw_view()
    assert ws.tab_transcript._model._show_raw_text is True
    assert "coh here" in ws.tab_transcript._model.data(ws.tab_transcript._model.index(0, 0))

    # Detailed summary, key points, follow-ups & explicit fallback banner
    assert ws.tab_summary.lbl_llm_fallback_banner.isHidden() is False
    assert "CPU fallback active" in ws.tab_summary.lbl_llm_fallback_banner.text()
    assert "Detailed summary paragraph" in ws.tab_summary.txt_detailed_summary.toPlainText()
    assert "Cohere selected" in ws.tab_summary.txt_key_points.toPlainText()
    assert "Share report" in ws.tab_summary.txt_follow_ups.toPlainText()

    # Ask rail scope selector ("This Meeting" vs "All Meetings")
    assert ws.combo_rail_scope.count() == 2

    # Settings Notetaker v2.0 model cards & Rebuild Knowledge Index button
    cfg = AppConfig(storage_dir=str(tmp_path / "cfg_data"), models_dir=str(ROOT_DIR / "data" / "models"))
    dummy_ctrl = SimpleNamespace(
        config=cfg,
        theme="light",
        db=db,
        get_meeting_models_status=lambda: MeetingModelManager(cfg.models_dir).get_all_statuses(),
        get_account_status=lambda: {"signed_in": False, "email": "", "plan": "free"},
        get_weekly_usage=lambda: {"dictation_words": 0, "meeting_minutes": 0},
        calendar_manager=None,
    )
    sw = SettingsWorkspaceWidget(dummy_ctrl)
    assert set(sw._meeting_model_rows.keys()) == {"meeting_asr", "meeting_diarization", "meeting_llm", "meeting_embeddings"}
    assert hasattr(sw, "btn_rebuild_knowledge")
    sw.teardown()
    ws.close()


def test_cpu_compaction_preserves_tail_and_caps_words():
    from voxlocal.engine.llm_engine import LocalLLMEngine, _compact_transcript_lines

    segs = [
        {"start_time": float(i * 10), "speaker": "Speaker_00", "text": f"line {i} " + ("word " * 25)}
        for i in range(400)
    ]
    compact = _compact_transcript_lines(segs, max_words=7000)
    assert len(compact.split()) <= 7000
    assert "[... concluding section ...]" in compact
    assert "line 399" in compact
    assert LocalLLMEngine._parse_json_safely("<think>reasoning</think>{\"ok\": true}") == {"ok": True}


def test_enterprise_laptop_safeguards_threads_igpu_and_low_ram(monkeypatch) -> None:
    from voxlocal.core.hardware import compute_safe_bg_threads
    from voxlocal.engine.llama_server_engine import LlamaServerManager

    # 1. Background CPU thread cap leaves headroom for UI, audio, Teams/Zoom
    assert compute_safe_bg_threads(4) == 2
    assert compute_safe_bg_threads(8) == 4
    assert compute_safe_bg_threads(12) == 6
    assert compute_safe_bg_threads(16) == 8

    gguf = ROOT_DIR / "data" / "models" / "meeting_llm" / "Qwen3.5-4B-Q4_K_M.gguf"

    # 2. Integrated GPU (free_vram is None) never attempts Vulkan -> safe CPU fallback
    mgr_igpu = LlamaServerManager(model_path=gguf, device="auto", ctx_size=24576)
    launched_variants = []
    monkeypatch.setattr(LlamaServerManager, "_get_free_vram_mb", staticmethod(lambda: None))
    monkeypatch.setattr(LlamaServerManager, "_get_memory_mb", staticmethod(lambda: (16384, 9000)))

    def _fake_launch_igpu(bin_p, variant):
        launched_variants.append(variant)
        mgr_igpu._active_device = "cpu" if variant == "cpu" else "vulkan_gpu"
        return True, f"mock-{variant}"

    monkeypatch.setattr(mgr_igpu, "_launch_variant", _fake_launch_igpu)
    assert mgr_igpu.ensure_started() is True
    assert launched_variants == ["cpu"]
    assert "integrated graphics" in mgr_igpu.fallback_reason.lower()

    # 3. Low-RAM OOM guard skips spawning LLM when < 2600 MB RAM free
    mgr_oom = LlamaServerManager(model_path=gguf, device="cpu", ctx_size=16384)
    monkeypatch.setattr(LlamaServerManager, "_get_memory_mb", staticmethod(lambda: (8192, 2000)))
    spawn_called = []
    monkeypatch.setattr(mgr_oom, "_launch_variant", lambda *a: spawn_called.append(a) or (True, "unexpected"))
    assert mgr_oom.ensure_started() is False
    assert spawn_called == []
    assert "2600 MB" in mgr_oom.fallback_reason

    # 4. 8 GB enterprise laptop with 4000 MB free RAM caps CPU context at 8192 & shorter idle timeout
    mgr_8gb = LlamaServerManager(model_path=gguf, device="cpu", ctx_size=24576, idle_timeout_sec=300)
    monkeypatch.setattr(LlamaServerManager, "_get_memory_mb", staticmethod(lambda: (8192, 4000)))
    if (gguf.exists() and mgr_8gb.is_available()):
        # Verify real binary args computation without running a full server
        import subprocess as _sp
        captured_cmds = []

        class _FakeProc:
            returncode = None
            def poll(self):
                return None
            def terminate(self):
                pass
            def wait(self, timeout=None):
                pass

        class _FakeResp:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        monkeypatch.setattr(_sp, "Popen", lambda cmd, **kw: captured_cmds.append(cmd) or _FakeProc())
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: _FakeResp())
        assert mgr_8gb.ensure_started() is True
        assert mgr_8gb.active_ctx_size == 8192
        assert mgr_8gb.idle_timeout_sec <= 90
        assert "-c" in captured_cmds[0] and "8192" in captured_cmds[0]
        assert "-ctk" in captured_cmds[0] and "q8_0" in captured_cmds[0]
        mgr_8gb._proc = None


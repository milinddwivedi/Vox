"""
Phase 2 Production Acceptance Test Suite.

Validates:
- Pure-NumPy Qwen3 128-mel parity (< 1e-3 max abs error vs HF AutoProcessor) & zero-transformers tokenizer
- All 16 mandatory reliability, fault-injection, dual-track isolation, selective-routing,
  transactional persistence, Stage 4 intelligence isolation, UI badge honesty, and rollback scenarios.

Strictly uses isolated temporary directories/DBs — never touches production data/voxlocal.db.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.audio.meeting_recorder import AudioInputMonitor, MeetingRecorder, MeetingRecordingResult
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.core.hardware import ExecutionProfile, HardwareProfiler
from voxlocal.engine.onnx_asr_engines import (
    GuardedQwen3OnnxEngine,
    NemotronStreamingEngine,
    OriserveApexVerifierEngine,
    ParakeetOnnxEngine,
    _Qwen3StandaloneTokenizer,
    _extract_spans_with_hard_cap,
    compute_qwen3_log_mel_features,
    discover_phase2_models,
)
from voxlocal.engine.selective_verifier import (
    SelectiveSecondPassVerifier,
    detect_hinglish_cues,
    evaluate_selective_triggers,
)
from voxlocal.engine.streaming_transcriber import LiveMeetingStreamingTranscriber
from voxlocal.storage.database import LocalDatabase


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


def _make_ctrl(tmp_path: Path, **cfg_overrides) -> AppController:
    storage = tmp_path / "data"
    storage.mkdir(parents=True, exist_ok=True)
    cfg = AppConfig(
        storage_dir=str(storage),
        models_dir=str(tmp_path / "models"),
        auto_record_calls=False,
        record_meeting_screen=False,
        **cfg_overrides,
    )
    cfg.disable_model_preload = True
    cfg.disable_hotkeys = True
    return AppController(config=cfg)


def _create_fake_model_tree(models_dir: Path) -> None:
    ch = models_dir / "challengers"
    q3 = ch / "sherpa-onnx-qwen3-asr-0.6b-int8"
    (q3 / "tokenizer").mkdir(parents=True, exist_ok=True)
    for fn in ("conv_frontend.onnx", "encoder.int8.onnx", "decoder.int8.onnx", "tokenizer/vocab.json", "tokenizer/merges.txt"):
        (q3 / fn).write_bytes(b"stub")
    for slug in ("sherpa-onnx-nemotron-streaming-en-0.6b-560ms-int8", "sherpa-onnx-parakeet-tdt-0.6b-v2-int8"):
        d = ch / slug
        d.mkdir(parents=True, exist_ok=True)
        for fn in ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"):
            (d / fn).write_bytes(b"stub")
    oa = ch / "oriserve-apex-ct2-int8"
    oa.mkdir(parents=True, exist_ok=True)
    (oa / "model.bin").write_bytes(b"stub")
    (oa / "config.json").write_text("{}", encoding="utf-8")


# ---------------------------------------------------------------------------
# 0. Feature Extractor Parity & Standalone Tokenizer Verification
# ---------------------------------------------------------------------------
def test_00_pure_numpy_mel_parity_and_standalone_tokenizer() -> None:
    real_q3_dir = ROOT_DIR / "data" / "models" / "challengers" / "sherpa-onnx-qwen3-asr-0.6b-int8" / "tokenizer"
    real_hf_dir = ROOT_DIR / "data" / "models" / "challengers" / "qwen3-asr-0.6b"
    if not real_q3_dir.exists() or not real_hf_dir.exists():
        pytest.skip("Local Qwen3 challenger tokenizer/processor files not present.")

    from transformers import AutoProcessor, AutoTokenizer

    hf_proc = AutoProcessor.from_pretrained(str(real_hf_dir), local_files_only=True, trust_remote_code=False)
    hf_tok = AutoTokenizer.from_pretrained(str(real_q3_dir), local_files_only=True)
    st = _Qwen3StandaloneTokenizer(real_q3_dir)

    assert len(st) == len(hf_tok)
    for sample_s in (
        "<|im_start|>system\n<|im_end|>\n<|im_start|>user\n<|audio_start|><|audio_pad|><|audio_end|><|im_end|>\n<|im_start|>assistant\n",
        "language English<asr_text>Daily scrum WFX PLM update.",
        "language Hindi<asr_text>अभी चेक करते हैं भाई।",
    ):
        assert st.encode(sample_s, add_special_tokens=False) == hf_tok.encode(sample_s, add_special_tokens=False)

    rng = np.random.default_rng(7)
    wav = (0.15 * rng.standard_normal(16000 * 3)).astype(np.float32)
    hf_mel = hf_proc.feature_extractor([wav], sampling_rate=16000, padding=False, return_tensors="np")["input_features"].transpose(0, 2, 1)
    np_mel = compute_qwen3_log_mel_features(wav)
    assert np_mel.shape == hf_mel.shape
    assert float(np.max(np.abs(np_mel - hf_mel))) < 1e-3

    # Edge case: very short audio (< 200 samples) must not crash reflect pad
    short_mel = compute_qwen3_log_mel_features(np.zeros(80, dtype=np.float32))
    assert short_mel.ndim == 3 and short_mel.shape[2] == 128


# ---------------------------------------------------------------------------
# Scenario 1 & 15: Lifecycle Stages + Transactional DB + Stage 4 Isolation
# ---------------------------------------------------------------------------
def test_01_normal_lifecycle_stages_provenance_and_stage4_isolation(tmp_path: Path, qapp: QApplication) -> None:
    ctrl = _make_ctrl(tmp_path)
    try:
        wav_m = tmp_path / "rec" / "mic_track.wav"
        wav_s = tmp_path / "rec" / "system_track.wav"
        wav_x = tmp_path / "rec" / "meeting_mixed.wav"
        tone = 0.05 * np.sin(2.0 * np.pi * 220.0 * np.linspace(0, 2.0, 32000, dtype=np.float32))
        _write_wav(wav_m, tone)
        _write_wav(wav_s, tone)
        _write_wav(wav_x, tone)

        fake_segs = [
            {
                "start_time": 0.0,
                "end_time": 1.8,
                "speaker": "You (Mic)",
                "source_track": "mic",
                "raw_text": "original raw text",
                "normalized_text": "original raw text",
                "text": "original raw text",
                "asr_model": "small",
                "asr_backend": "faster_whisper",
                "asr_device": "cpu",
                "asr_compute_type": "int8",
                "processing_history": [{"stage": "offline_primary", "model": "small"}],
            }
        ]
        ctrl.transcriber = MagicMock()
        ctrl.transcriber.transcribe_meeting_dual_track.return_value = fake_segs
        ctrl.transcriber.reconcile_meeting_transcript.side_effect = lambda **kw: kw["segments"]
        ctrl.transcriber.last_track_stats = {
            "mic": {"samples_captured": 32000, "samples_processed": 32000, "cursor_sec": 2.0},
            "system": {"samples_captured": 32000, "samples_processed": 32000, "cursor_sec": 2.0},
        }

        mid = ctrl.db.create_meeting(title="Lifecycle + Stage4 Fault", model_used="small")
        # Inject live stable segment first
        ctrl.db.add_transcript_segment(
            meeting_id=mid,
            segment_index=1,
            start_time=0.0,
            end_time=1.8,
            speaker="You (Mic)",
            text="original live text",
            raw_text="original live raw",
            source_track="mic",
            transcript_stage="stable",
            transcript_state="stable",
        )
        assert ctrl.db.get_meeting_segments(mid)[0]["transcript_state"] == "stable"

        # Break Stage 4 summarizer intentionally: saved transcript & completed status MUST survive!
        res_obj = MeetingRecordingResult(
            duration_sec=2.0,
            mixed_wav_path=str(wav_x),
            mic_wav_path=str(wav_m),
            sys_wav_path=str(wav_s),
            mic_device_name="TestMic",
            loopback_device_name="TestLoop",
            capture_mode="both",
            audio_health={"overall_status": "healthy"},
        )
        with patch("voxlocal.core.controller.LocalMeetingSummarizer.analyze_full", side_effect=RuntimeError("LLM OOM")):
            ctrl._finalize_meeting_worker(mid, res_obj, video_path="")

        mtg = ctrl.db.get_meeting(mid)
        assert mtg is not None
        assert mtg["status"] == "completed"
        segs = ctrl.db.get_meeting_segments(mid)
        assert len(segs) == 1
        assert segs[0]["raw_text"] == "original raw text"
        assert segs[0]["transcript_state"] in ("final", "verified")
        hist = json.loads(segs[0]["processing_history_json"])
        assert hist and hist[0]["stage"] == "offline_primary"
        comp = json.loads(mtg["completeness_report_json"])
        assert "intelligence_isolated_error" in " ".join(comp.get("fallback_notes", []))

        # DB Non-Empty Replace Guard & Duplicate Protection
        ctrl.db.replace_meeting_segments(mid, [])
        assert len(ctrl.db.get_meeting_segments(mid)) == 1
        id1 = ctrl.db.add_transcript_segment(meeting_id=mid, start_time=5.0, end_time=6.0, speaker="Mic", text="Hello world.", raw_text="raw hello", source_track="mic")
        id2 = ctrl.db.add_transcript_segment(meeting_id=mid, start_time=5.1, end_time=6.1, speaker="Mic", text="hello world", raw_text="norm hello", source_track="mic")
        assert id1 == id2
    finally:
        ctrl.shutdown()


# ---------------------------------------------------------------------------
# Scenarios 2, 3, 4: Audio Integrity, Disconnects, Zero-Byte, Silence, Clipping
# ---------------------------------------------------------------------------
def test_02_03_04_audio_integrity_disconnect_zerobyte_silence_clipping(tmp_path: Path) -> None:
    mon = AudioInputMonitor()
    mon.configure_tracks(mic_enabled=True, system_enabled=True)

    # Normal mic callback + active speech then disconnect on mic; system continues
    mon.record_callback("mic", now_mono=10.0)
    mon.record_callback("system", now_mono=10.0)
    speech = (0.10 * np.sin(2.0 * np.pi * 200.0 * np.linspace(0, 1.0, 16000, dtype=np.float32))).astype(np.float32)
    mon.observe("mic", speech, dt_sec=1.0)
    mon.observe("system", speech, dt_sec=1.0)

    warns1 = mon.heartbeat_tick(elapsed_sec=14.0, mic_active=False, sys_active=True, now_mono=15.0)
    assert any("disconnected" in w.lower() for w in warns1)
    snap1 = mon.snapshot()
    assert snap1["mic"]["disconnect_events"] >= 1
    assert snap1["mic"]["stream_interruptions"] >= 1
    assert snap1["system"]["status"] == "healthy"

    # Zero-byte check when mic has 0 samples & 0 callbacks after 4.5s
    mon_zb = AudioInputMonitor()
    mon_zb.configure_tracks(mic_enabled=True, system_enabled=False)
    warns_zb = mon_zb.heartbeat_tick(elapsed_sec=4.5, mic_active=True, sys_active=False, now_mono=100.0)
    assert any("0 bytes" in w.lower() for w in warns_zb)
    assert mon_zb.snapshot()["mic"]["zero_byte_events"] == 1

    # Silence stall (>=12s) & Clipping (>2% samples >= 0.98)
    mon_sc = AudioInputMonitor()
    mon_sc.configure_tracks(mic_enabled=True, system_enabled=True)
    mon_sc.observe("mic", np.zeros(16000, dtype=np.float32), dt_sec=13.0)
    clipped_sys = np.full(32000, 0.995, dtype=np.float32)
    mon_sc.observe("system", clipped_sys, dt_sec=2.0)
    warns_sc = mon_sc.heartbeat_tick(elapsed_sec=13.5, mic_active=True, sys_active=True, now_mono=200.0)
    assert any("silent" in w.lower() for w in warns_sc)
    assert any("clipping" in w.lower() for w in warns_sc)
    snap_sc = mon_sc.snapshot()
    assert snap_sc["mic"]["silence_stall_count"] >= 1
    assert snap_sc["system"]["clipping_ratio"] > 0.9


# ---------------------------------------------------------------------------
# Scenarios 5 & 6: Hardware-Aware Selection, CPU-only & Insufficient VRAM
# ---------------------------------------------------------------------------
def test_05_06_hardware_cpu_only_and_vram_gates(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    _create_fake_model_tree(models_dir)
    prof_base = dict(
        os_name="Windows 11", os_version="10.0", os_build="22631", is_64bit=True,
        cpu_name="Test CPU", cpu_cores=8, cpu_threads=16, ram_total_gb=16.0, ram_avail_gb=9.0,
        gpu_name="GTX 1650", gpu_type="NVIDIA CUDA", gpu_vram_gb=4.0,
        cuda_available=True, cuda_device_count=1, disk_free_gb=50.0,
        corporate_tier="Tier 2", adaptive_cpu_threads=6, recommended_whisper_model="small",
        is_minimum_met=True, is_recommended_met=True,
    )

    cfg_auto = AppConfig(storage_dir=str(tmp_path / "data"), models_dir=str(models_dir), enable_phase2_asr_pipeline=True, asr_session_mode="auto")
    cfg_en = AppConfig(storage_dir=str(tmp_path / "data"), models_dir=str(models_dir), enable_phase2_asr_pipeline=True, asr_session_mode="english")

    # CPU-only system: 0 GPU dependency (v2.0 post-call Cohere EN single-winner profile)
    cpu_prof = SimpleNamespace(**{**prof_base, "cuda_available": False, "gpu_name": "Intel Iris", "gpu_type": "CPU Only"})
    with patch.object(HardwareProfiler, "get_profile", return_value=cpu_prof), \
         patch.object(HardwareProfiler, "_query_live_gpu_vram_mb", return_value=(None, None)), \
         patch.object(HardwareProfiler, "_check_hf_asr_runtime_available", return_value=False):
        ep_auto = HardwareProfiler.select_execution_profile(config=cfg_auto)
        assert ep_auto.live_backend == "disabled"
        assert ep_auto.live_device == "cpu"
        assert ep_auto.live_execution_mode == "post_call_only"
        assert ep_auto.offline_backend == "sherpa_onnx_cohere"
        assert ep_auto.offline_model == "cohere-transcribe-2b-en-int8"
        assert ep_auto.selective_verifier_backend == "disabled"

        ep_en = HardwareProfiler.select_execution_profile(config=cfg_en)
        assert ep_en.live_backend == "disabled"
        assert ep_en.live_execution_mode == "post_call_only"
        assert ep_en.offline_backend == "sherpa_onnx_cohere"
        assert ep_en.selective_verifier_backend == "disabled"

    # Insufficient VRAM (< 2800 MB): stays in Tier 2 standard CPU profile
    gpu_prof = SimpleNamespace(**prof_base)
    with patch.object(HardwareProfiler, "get_profile", return_value=gpu_prof), \
         patch.object(HardwareProfiler, "_query_live_gpu_vram_mb", return_value=(3200, 800)), \
         patch.object(HardwareProfiler, "_check_hf_asr_runtime_available", return_value=True), \
         patch.object(HardwareProfiler, "_has_local_hf_asr_checkpoint", return_value=True):
        ep_low = HardwareProfiler.select_execution_profile(config=cfg_auto)
        assert ep_low.tier_id == "tier2_standard"
        assert ep_low.offline_backend == "sherpa_onnx_cohere"
        assert ep_low.selective_verifier_backend == "disabled"

    # Moderate VRAM (1400 MB): Tier 2 CPU ASR without double-loading heavy GPU models
    with patch.object(HardwareProfiler, "get_profile", return_value=gpu_prof), \
         patch.object(HardwareProfiler, "_query_live_gpu_vram_mb", return_value=(2600, 1400)), \
         patch.object(HardwareProfiler, "_check_hf_asr_runtime_available", return_value=True), \
         patch.object(HardwareProfiler, "_has_local_hf_asr_checkpoint", return_value=True):
        ep_mid = HardwareProfiler.select_execution_profile(config=cfg_auto)
        assert ep_mid.tier_id == "tier2_standard"
        assert ep_mid.offline_backend == "sherpa_onnx_cohere"

    # High VRAM (>= 2800 MB): unlocks Tier 3 performance profile (Vulkan LLM + CPU Cohere ASR)
    with patch.object(HardwareProfiler, "get_profile", return_value=gpu_prof), \
         patch.object(HardwareProfiler, "_query_live_gpu_vram_mb", return_value=(1000, 3000)), \
         patch.object(HardwareProfiler, "_check_hf_asr_runtime_available", return_value=True), \
         patch.object(HardwareProfiler, "_has_local_hf_asr_checkpoint", return_value=True):
        ep_hi = HardwareProfiler.select_execution_profile(config=cfg_auto)
        assert ep_hi.tier_id == "tier3_performance"
        assert ep_hi.offline_backend == "sherpa_onnx_cohere"
        assert ep_hi.selective_verifier_backend == "disabled"


# ---------------------------------------------------------------------------
# Scenarios 7 & 8: Model Load Failure & Inference Failure Mid-Live / Mid-Final
# ---------------------------------------------------------------------------
def test_07_08_model_load_and_inference_failures_preserve_transcripts(tmp_path: Path, qapp: QApplication) -> None:
    db = LocalDatabase(tmp_path / "fail_test.db")
    try:
        mid = db.create_meeting(title="Model Fail Test", model_used="qwen3-asr-0.6b-onnx-int8")
        # 1. Missing/corrupted ONNX dir causes explicit clean failure, then live falls back to Whisper cleanly
        bad_eng = GuardedQwen3OnnxEngine(onnx_dir=tmp_path / "nonexistent_dir")
        ok_bad, msg_bad = bad_eng.ensure_loaded()
        assert ok_bad is False and "not found" in msg_bad.lower()

        fake_tr = MagicMock()
        fake_tr.is_model_loaded.return_value = True
        st = LiveMeetingStreamingTranscriber(transcriber=fake_tr, db=db)
        st._meeting_id = mid
        st._live_backend = "qwen3_onnx"
        st._is_incremental = False
        st._live_execution_mode = "endpointed"
        st._fallback_to_whisper("Simulated ONNX load error")
        tel = st.get_stream_telemetry()
        assert st._live_backend == "faster_whisper"
        assert tel["fallback_events"] == 1
        assert "Simulated ONNX load error" in tel["fallback_reasons"][0]
    finally:
        db.close()

    # 2. Mid-finalization offline failure preserves pre-existing live stable segments in DB!
    ctrl = _make_ctrl(tmp_path)
    try:
        wav_x = tmp_path / "rec_fail" / "mixed.wav"
        _write_wav(wav_x, 0.05 * np.sin(2.0 * np.pi * 200.0 * np.linspace(0, 1.5, 24000, dtype=np.float32)))
        mid = ctrl.db.create_meeting(title="Offline Failure Rescue", model_used="small")
        ctrl.db.add_transcript_segment(
            meeting_id=mid,
            segment_index=1,
            start_time=0.0,
            end_time=1.4,
            speaker="Speaker 1",
            text="Important live captured utterance",
            raw_text="important live captured utterance",
            source_track="system",
            transcript_stage="stable",
            transcript_state="stable",
        )
        ctrl.transcriber = MagicMock()
        ctrl.transcriber.transcribe_meeting_dual_track.side_effect = RuntimeError("Simulated offline decoder crash")
        ctrl.transcriber.reconcile_meeting_transcript.side_effect = lambda **kw: kw["segments"]
        res_obj = MeetingRecordingResult(
            duration_sec=1.5, mixed_wav_path=str(wav_x), mic_wav_path="", sys_wav_path="",
            mic_device_name="", loopback_device_name="", capture_mode="mixed",
        )
        ctrl._finalize_meeting_worker(mid, res_obj)
        segs_after = ctrl.db.get_meeting_segments(mid)
        assert len(segs_after) == 1
        assert "important live captured utterance" in segs_after[0]["text"].lower()
    finally:
        ctrl.shutdown()


# ---------------------------------------------------------------------------
# Scenarios 9, 10, 11: Crash Recovery, Restart, Interrupted Cancellation
# ---------------------------------------------------------------------------
def test_09_10_11_crash_restart_and_cancellation_honesty(tmp_path: Path, qapp: QApplication) -> None:
    db_path = tmp_path / "data" / "voxlocal.db"
    wav_c = tmp_path / "crashed.wav"
    _write_wav(wav_c, 0.05 * np.sin(2.0 * np.pi * 220.0 * np.linspace(0, 1.0, 16000, dtype=np.float32)))

    pre_db = LocalDatabase(db_path)
    mid_rec_crash = pre_db.create_meeting(title="Crashed Mid Recording", audio_path=str(wav_c))
    pre_db.add_transcript_segment(
        meeting_id=mid_rec_crash, segment_index=1, start_time=0.0, end_time=1.0,
        speaker="You (Mic)", text="Survived crash", source_track="mic", transcript_stage="stable",
    )
    mid_fin_crash = pre_db.create_meeting(title="Crashed Mid Finalizing", audio_path=str(wav_c))
    pre_db.update_meeting(mid_fin_crash, status="transcribing", duration_sec=1.0)
    pre_db.enqueue_finalization_job(mid_fin_crash, audio_path=str(wav_c), duration_sec=1.0)
    pre_db.update_finalization_job(mid_fin_crash, state="FINALIZING", last_stage="running")
    pre_db.close()

    resumed_ids = []
    done_ev = threading.Event()

    def _spy(self_c, m_id, res, video_path=""):
        resumed_ids.append(m_id)
        self_c.db.update_meeting(m_id, status="completed")
        self_c.db.update_finalization_job(m_id, state="COMPLETED", last_stage="completed", last_error="")
        done_ev.set()

    with patch.object(AppController, "_finalize_meeting_worker", _spy):
        ctrl = _make_ctrl(tmp_path)
        try:
            assert done_ev.wait(timeout=4.0)
            assert mid_fin_crash in resumed_ids
            # Recording-phase crash marks meeting honestly as 'interrupted' while preserving live segments!
            rec_row = ctrl.db.get_meeting(mid_rec_crash)
            assert rec_row["status"] == "interrupted"
            assert len(ctrl.db.get_meeting_segments(mid_rec_crash)) == 1
        finally:
            ctrl.shutdown()

    # Scenario 11: Cancelled finalization NEVER falsely marks meeting 'completed'
    ctrl2 = _make_ctrl(tmp_path / "cancel_env")
    try:
        mid_cancel = ctrl2.db.create_meeting(title="Cancelled Job", audio_path=str(wav_c))
        ctrl2.db.enqueue_finalization_job(mid_cancel, audio_path=str(wav_c), duration_sec=1.0)
        cancel_ev = threading.Event()
        cancel_ev.set()
        res_obj = MeetingRecordingResult(
            duration_sec=1.0, mixed_wav_path=str(wav_c), mic_wav_path="", sys_wav_path="",
            mic_device_name="", loopback_device_name="", capture_mode="mixed",
        )
        ctrl2._finalize_meeting_worker(mid_cancel, res_obj, cancel_event=cancel_ev)
        assert ctrl2.db.get_meeting(mid_cancel)["status"] == "interrupted"
    finally:
        ctrl2.shutdown()


# ---------------------------------------------------------------------------
# Scenarios 12, 13, 14: Two Consecutive Calls, Short & Long (>30s), Track Isolation
# ---------------------------------------------------------------------------
def test_12_13_14_consecutive_calls_long_eof_and_track_isolation(tmp_path: Path, qapp: QApplication) -> None:
    # Scenario 13: Long >30s chunking covers 100% up to EOF without dropping final tail
    sr = 16000
    long_aud = np.zeros(int(36.0 * sr), dtype=np.float32)
    t = np.linspace(0, 1.0, sr, dtype=np.float32)
    # Speech bursts at 0-3s and trailing 34.5-35.8s
    long_aud[0 : 3 * sr] = 0.08 * np.sin(2.0 * np.pi * 220.0 * np.linspace(0, 3.0, 3 * sr, dtype=np.float32))
    long_aud[int(34.5 * sr) : int(35.8 * sr)] = 0.08 * np.sin(2.0 * np.pi * 240.0 * np.linspace(0, 1.3, int(1.3 * sr), dtype=np.float32))
    spans = _extract_spans_with_hard_cap(long_aud, max_chunk_sec=22.0, hard_cap_sec=26.0)
    assert spans and spans[-1][1] >= int(35.5 * sr)
    assert all((e - s) <= int(26.0 * sr) for s, e in spans)

    # Scenario 14: Dual-track independent cursors do NOT drop simultaneous mic + system speech
    fake_tr = MagicMock()
    db = LocalDatabase(tmp_path / "dual_track.db")
    try:
        mid = db.create_meeting(title="Dual Track Isolation")
        committed = []
        st = LiveMeetingStreamingTranscriber(
            transcriber=fake_tr, db=db, on_confirmed=lambda m_id, s: committed.append(s)
        )
        # Mock Phase 2 endpointed engine returning distinct text per track
        mock_eng = MagicMock()
        mock_eng.is_loaded = True
        mock_eng.get_active_asr_provenance.return_value = {
            "asr_model": "qwen3-asr-0.6b-onnx-int8",
            "asr_backend": "qwen3_onnx",
            "asr_device": "cpu",
            "asr_compute_type": "int8",
        }
        mock_eng.transcribe_array.side_effect = [
            {"raw_text": "remote speaking", "text": "Remote speaking", "detected_language": "en", "avg_logprob": -0.1},
            {"raw_text": "mic speaking", "text": "Mic speaking", "detected_language": "en", "avg_logprob": -0.1},
        ]
        st._meeting_id = mid
        st._live_backend = "qwen3_onnx"
        st._live_execution_mode = "endpointed"
        st._is_incremental = False
        st._phase2_engine = mock_eng
        st._recorder = SimpleNamespace(elapsed_seconds=3.0)
        tone_1s = (0.05 * np.sin(2.0 * np.pi * 220.0 * t)).astype(np.float32)
        st._flush_endpointed_track_utterance(mock_eng, "system", "Speaker 1", 0.0, 1.5, tone_1s)
        st._flush_endpointed_track_utterance(mock_eng, "mic", "You (Mic)", 0.2, 1.8, tone_1s)
        assert [c["source_track"] for c in committed] == ["system", "mic"]
        assert st.get_stream_telemetry()["execution_mode"] == "endpointed"
        assert st.get_stream_telemetry()["is_incremental"] is False
    finally:
        db.close()

    # Scenario 12: Consecutive meetings switch_to_meeting without crossover
    ctrl = _make_ctrl(tmp_path / "consec_env")
    try:
        ctrl.meeting_recorder = MagicMock()
        ctrl.meeting_recorder.is_recording = False
        ctrl.meeting_recorder.state = "idle"
        ctrl.meeting_recorder.start.return_value = (True, "OK")
        ctrl.meeting_recorder.stop.return_value = None

        ok1, _ = ctrl.start_meeting_recording(title="Call A", recording_mode="audio_only")
        assert ok1
        mid_a = ctrl.active_meeting_id
        ctrl.meeting_recorder.is_recording = True
        ok2, _ = ctrl.switch_to_meeting(new_title="Call B", recording_mode="audio_only")
        assert ok2
        mid_b = ctrl.active_meeting_id
        assert mid_a != mid_b
    finally:
        ctrl.shutdown()


# ---------------------------------------------------------------------------
# Scenario 16: Rollback Switch & Selective Language Routing Guards
# ---------------------------------------------------------------------------
def test_16_rollback_switch_and_selective_routing_guards(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    _create_fake_model_tree(models_dir)

    # A. Rollback switch (enable_phase2_asr_pipeline=False) cleanly reverts HardwareProfiler to faster_whisper
    cfg_rb = AppConfig(
        storage_dir=str(tmp_path / "data"),
        models_dir=str(models_dir),
        enable_phase2_asr_pipeline=False,
        asr_session_mode="auto",
    )
    ep_rb = HardwareProfiler.select_execution_profile(config=cfg_rb)
    assert ep_rb.phase2_enabled is False
    assert ep_rb.live_backend in ("disabled", "faster_whisper", "hf_qwen3_asr")
    assert ep_rb.selective_verifier_backend == "disabled"

    # B. Homograph exclusion: Indian/US English using 'to', 'me', 'par' MUST NEVER trigger Hinglish cues!
    is_hi_en, _ = detect_hinglish_cues("Send that to me on par with last quarter release.", detected_language="en")
    assert is_hi_en is False
    is_hi_real, _ = detect_hinglish_cues("Abhi woh issue check karna hai kyunki nahi chal raha", detected_language="hi")
    assert is_hi_real is True

    # C. Selective verifier skips English-to-Oriserve and Hinglish-to-Parakeet WITHOUT loading any verifier model!
    verifier = SelectiveSecondPassVerifier(models_dir=models_dir)
    segs_en = [
        {"start_time": 0.0, "end_time": 2.0, "text": "Go to me par test", "raw_text": "go to me par test", "raw_model_score": -0.60, "detected_language": "en"}
    ]
    ep_ori = ExecutionProfile(
        tier_id="t3", tier_label="T3", live_backend="qwen3_onnx", live_model="q", live_device="cpu", live_compute_type="int8",
        offline_backend="qwen3_onnx", offline_model="q", offline_device="cpu", offline_compute_type="int8",
        safe_cpu_threads=4, live_best_effort=True, max_concurrent_models=1, hf_runtime_available=False, free_vram_mb=1400,
        session_mode="auto", selective_verifier_backend="oriserve_apex_ct2", phase2_enabled=True,
    )
    with patch.object(OriserveApexVerifierEngine, "ensure_loaded") as mock_ori_load:
        out_en, stats_en = verifier.verify_segments(segs_en, execution_profile=ep_ori, config=AppConfig(selective_second_pass_enabled=True))
        assert mock_ori_load.called is False
        assert stats_en["skipped_guard_count"] == 1
        assert "english_never_routed_to_oriserve" in json.dumps(out_en[0]["processing_history"])

    ep_para = ExecutionProfile(
        tier_id="t2", tier_label="T2", live_backend="nemotron_onnx", live_model="n", live_device="cpu", live_compute_type="int8",
        offline_backend="parakeet_onnx", offline_model="p", offline_device="cpu", offline_compute_type="int8",
        safe_cpu_threads=4, live_best_effort=True, max_concurrent_models=1, hf_runtime_available=False, free_vram_mb=None,
        session_mode="auto", selective_verifier_backend="parakeet_onnx", phase2_enabled=True,
    )
    segs_hi = [
        {"start_time": 0.0, "end_time": 2.0, "text": "Abhi woh check karna hai kyunki issue hai", "raw_text": "अभी वो चेक करना है", "raw_model_score": -0.60, "detected_language": "hi"}
    ]
    with patch.object(ParakeetOnnxEngine, "ensure_loaded") as mock_pk_load:
        out_hi, stats_hi = verifier.verify_segments(segs_hi, execution_profile=ep_para, config=AppConfig(selective_second_pass_enabled=True))
        assert mock_pk_load.called is False
        assert stats_hi["skipped_guard_count"] == 1
        assert "hinglish_never_routed_to_english_only" in json.dumps(out_hi[0]["processing_history"])


# ---------------------------------------------------------------------------
# 17. Selective Verifier Positive Acceptance & UI Badge Honesty
# ---------------------------------------------------------------------------
def test_17_selective_verifier_positive_acceptance_and_ui_badge_honesty(tmp_path: Path, qapp: QApplication) -> None:
    models_dir = tmp_path / "models"
    _create_fake_model_tree(models_dir)
    wav_s = tmp_path / "sys_pos.wav"
    sr = 16000
    t = np.linspace(0, 2.0, 2 * sr, dtype=np.float32)
    _write_wav(wav_s, 0.08 * np.sin(2.0 * np.pi * 220.0 * t))

    verifier = SelectiveSecondPassVerifier(models_dir=models_dir)
    ep = ExecutionProfile(
        tier_id="t3", tier_label="T3", live_backend="qwen3_onnx", live_model="qwen3-asr-0.6b-onnx-int8",
        live_device="cpu", live_compute_type="int8", offline_backend="qwen3_onnx",
        offline_model="qwen3-asr-0.6b-onnx-int8", offline_device="cpu", offline_compute_type="int8",
        safe_cpu_threads=4, live_best_effort=True, max_concurrent_models=1,
        hf_runtime_available=False, free_vram_mb=1600,
        session_mode="auto", selective_verifier_backend="oriserve_apex_ct2", phase2_enabled=True,
    )
    seg_in = [
        {
            "start_time": 0.0,
            "end_time": 2.0,
            "speaker": "Speaker 1",
            "source_track": "system",
            "raw_text": "अभी वो चेक करना है",
            "normalized_text": "abhi woh check karna hai",
            "text": "abhi woh check karna hai",
            "raw_model_score": -0.58,
            "detected_language": "hi",
            "asr_backend": "qwen3_onnx",
            "processing_history": [{"stage": "offline_primary", "model": "qwen3-asr-0.6b-onnx-int8"}],
        }
    ]
    unload_spy = MagicMock()
    with patch.object(OriserveApexVerifierEngine, "ensure_loaded", return_value=(True, "OK")), \
         patch.object(OriserveApexVerifierEngine, "transcribe_slice", return_value={
             "raw_text": "अभी वह WFX issue check करना है",
             "text": "Abhi wah WFX issue check karna hai",
             "detected_language": "hi",
             "avg_logprob": -0.18,
             "had_utf8_replacement": False,
         }), \
         patch.object(OriserveApexVerifierEngine, "unload_model", unload_spy):
        out_segs, st = verifier.verify_segments(
            seg_in,
            execution_profile=ep,
            config=AppConfig(
                storage_dir=str(tmp_path / "data"),
                models_dir=str(models_dir),
                selective_second_pass_enabled=True,
            ),
            sys_wav_path=str(wav_s),
        )
    assert st["escalated_count"] == 1
    assert st["accepted_count"] == 1
    assert unload_spy.called is True
    assert out_segs[0]["raw_text"] == "अभी वो चेक करना है"
    assert out_segs[0]["normalized_text"] == "abhi woh check karna hai"
    assert out_segs[0]["corrected_text"] == "Abhi wah WFX issue check karna hai"
    assert out_segs[0]["text"] == "Abhi wah WFX issue check karna hai"
    assert out_segs[0]["transcript_state"] == "verified"
    assert any(h.get("stage") == "selective_second_pass_accepted" for h in out_segs[0]["processing_history"])

    # UI Badge Honesty check (Post-Call stages vs Completed)
    from voxlocal.ui.notetaker_page import NotetakerPage

    assert "Post-Call" in NotetakerPage._format_status_badge("recording", "")
    assert "Transcribing" in NotetakerPage._format_status_badge("finalizing", "transcribing")
    assert "Verifying" in NotetakerPage._format_status_badge("finalizing", "verifying")
    assert "Completed" in NotetakerPage._format_status_badge("completed", "completed")

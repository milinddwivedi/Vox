"""
Comprehensive Phase 3 Release-Candidate Tests for VOX Speech Intelligence.

Covers:
- Phase 3B Hardware-adaptive execution profile selection (Tier 1/2/3, low-VRAM CPU fallback, missing HF runtime).
- Portable hardware diagnostics CLI (`main.py --hardware-diagnostics` & `scripts/vox_hardware_diagnostics.py`).
- Phase 3A/3C Optional HF ASR engine (`hf_asr_engine.py`: lazy imports, UTF-8 sanitization, safe unload).
- Phase 3C Live tail flush & sample-accurate cursor completion (`LiveMeetingStreamingTranscriber.stop()`).
- Phase 3D Completeness engine (`completed` vs `incomplete` on partial coverage vs `finalization_failed`,
  silence hallucinations, speech without words, uncertain segment annotations).
- Phase 3C/3F Verbatim protection, separated `cleaned_transcript`, `raw_text`, `is_uncertain`, and DB persistence.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"
for _p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from voxlocal.core.config import AppConfig
from voxlocal.core.hardware import HardwareProfile, HardwareProfiler
from voxlocal.engine.completeness import (
    build_completeness_report,
    detect_possible_silence_hallucinations,
    mark_uncertain_segments,
)
from voxlocal.engine.hf_asr_engine import LocalHFASRTranscriber, sanitize_utf8_text
from voxlocal.storage.database import LocalDatabase


def _write_wav(path: Path, samples_16k: np.ndarray, sample_rate: int = 16000) -> None:
    pcm = np.clip(samples_16k * 32767.0, -32768.0, 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def _fake_hw_profile(
    *,
    ram_total_gb: float = 16.0,
    ram_avail_gb: float = 8.0,
    cpu_cores: int = 4,
    cpu_threads: int = 8,
    cuda_available: bool = False,
    gpu_vram_gb: float = 0.0,
) -> HardwareProfile:
    return HardwareProfile(
        os_name="Windows 11",
        os_version="10.0.22631",
        os_build="22631",
        is_64bit=True,
        cpu_name="Simulated Laptop CPU",
        cpu_cores=cpu_cores,
        cpu_threads=cpu_threads,
        ram_total_gb=ram_total_gb,
        ram_avail_gb=ram_avail_gb,
        gpu_name="Intel Iris Xe Graphics" if not cuda_available else "NVIDIA GeForce GTX 1650",
        gpu_type="CPU Only" if not cuda_available else "NVIDIA CUDA Acceleration",
        gpu_vram_gb=gpu_vram_gb,
        cuda_available=cuda_available,
        cuda_device_count=1 if cuda_available else 0,
        disk_free_gb=50.0,
        corporate_tier="Simulated",
        adaptive_cpu_threads=min(cpu_threads, 6),
        recommended_whisper_model="small",
        is_minimum_met=True,
        is_recommended_met=(ram_total_gb >= 15.0 and cpu_threads >= 6),
    )


def test_hardware_tier_selection_across_tiers_and_fallbacks():
    cfg = AppConfig(enable_hf_asr_rc=True, hf_asr_model_id="Qwen/Qwen3-ASR-0.6B-hf")

    # Tier 1: 8 GB / 4 threads CPU-only laptop -> base live, small offline, CPU int8
    t1_prof = _fake_hw_profile(ram_total_gb=8.0, ram_avail_gb=3.2, cpu_cores=4, cpu_threads=4, cuda_available=False)
    with patch.object(HardwareProfiler, "get_profile", return_value=t1_prof), patch.object(
        HardwareProfiler, "_check_hf_asr_runtime_available", return_value=False
    ):
        ep1 = HardwareProfiler.select_execution_profile(config=cfg)
    assert ep1.tier_id == "tier1_minimum"
    assert ep1.live_backend == "faster_whisper"
    assert ep1.live_model == "base"
    assert ep1.offline_model == "small"
    assert ep1.live_device == "cpu"
    assert ep1.offline_device == "cpu"
    assert ep1.max_concurrent_models == 1
    assert ep1.live_best_effort is True

    # Tier 2: 16 GB standard CPU laptop (integrated graphics) -> small live, large-v3-turbo offline, CPU int8
    t2_prof = _fake_hw_profile(ram_total_gb=16.0, ram_avail_gb=9.0, cpu_cores=6, cpu_threads=12, cuda_available=False)
    with patch.object(HardwareProfiler, "get_profile", return_value=t2_prof), patch.object(
        HardwareProfiler, "_check_hf_asr_runtime_available", return_value=False
    ):
        ep2 = HardwareProfiler.select_execution_profile(config=cfg)
    assert ep2.tier_id == "tier2_standard"
    assert ep2.live_model == "small"
    assert ep2.offline_model == "large-v3-turbo"
    assert ep2.offline_device == "cpu"
    assert ep2.max_concurrent_models == 1

    # Tier 2 fallback when CUDA exists on a workstation, but external app leaves < 2600 MB free VRAM
    t2_vram_low = _fake_hw_profile(ram_total_gb=32.0, ram_avail_gb=12.0, cpu_cores=8, cpu_threads=16, cuda_available=True, gpu_vram_gb=4.0)
    with patch.object(HardwareProfiler, "get_profile", return_value=t2_vram_low), patch.object(
        HardwareProfiler, "_query_live_gpu_vram_mb", return_value=(2800, 1200)
    ):
        ep2_low = HardwareProfiler.select_execution_profile(config=cfg)
    assert ep2_low.tier_id == "tier2_standard"
    assert ep2_low.offline_device == "cpu"
    assert "1200 MB" in ep2_low.fallback_reason

    # Tier 3: Performance GPU w/ >= 2600 MB free VRAM & RC flag on & HF checkpoint present -> hf_qwen3_asr offline
    with patch.object(HardwareProfiler, "get_profile", return_value=t2_vram_low), patch.object(
        HardwareProfiler, "_query_live_gpu_vram_mb", return_value=(800, 3200)
    ), patch.object(
        HardwareProfiler, "_check_hf_asr_runtime_available", return_value=True
    ), patch.object(
        HardwareProfiler, "_has_local_hf_asr_checkpoint", return_value=True
    ):
        ep3 = HardwareProfiler.select_execution_profile(config=cfg)
    assert ep3.tier_id == "tier3_performance"
    assert ep3.offline_backend == "hf_qwen3_asr"
    assert ep3.offline_model == "Qwen/Qwen3-ASR-0.6B-hf"
    assert ep3.offline_device == "cuda"
    assert ep3.live_backend == "faster_whisper"
    assert ep3.live_model == "large-v3-turbo"
    assert ep3.max_concurrent_models == 1


def test_portable_hardware_diagnostics_cli_and_script():
    res_main = subprocess.run(
        [sys.executable, str(ROOT_DIR / "main.py"), "--hardware-diagnostics"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert res_main.returncode == 0, res_main.stderr
    json_start = res_main.stdout.find("{")
    assert json_start >= 0
    payload = json.loads(res_main.stdout[json_start:])
    assert "execution_profile" in payload
    assert payload["execution_profile"]["max_concurrent_models"] == 1
    assert payload["hardware_tier"] in ("tier1_minimum", "tier2_standard", "tier3_performance")


def test_hf_asr_utf8_sanitization_and_missing_runtime_safety():
    clean, had_rep = sanitize_utf8_text("baat\ufffd kar rahe hai\ufffd")
    assert had_rep is True
    assert "\ufffd" not in clean
    assert "baat" in clean and "kar rahe hai" in clean

    # Verify graceful refusal if torch/transformers are absent (e.g., standard Windows laptop build)
    eng = LocalHFASRTranscriber(model_id="Qwen/Qwen3-ASR-0.6B-hf")
    with patch.object(LocalHFASRTranscriber, "is_runtime_available", return_value=False):
        ok, msg = eng.ensure_loaded(local_only=True)
    assert ok is False
    assert "not installed" in msg.lower()


def test_completeness_report_blocks_false_completed_on_partial_coverage():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        sr = 16000
        dur = 10.0
        t = np.linspace(0, dur, int(dur * sr), dtype=np.float32)
        # Speech burst at 1..3s and 7..9s; silent around 4..6s
        mic_sig = np.zeros_like(t)
        mic_sig[int(1.0 * sr) : int(3.0 * sr)] = 0.05 * np.sin(2 * np.pi * 240.0 * t[: 2 * sr])
        mic_sig[int(7.0 * sr) : int(9.0 * sr)] = 0.05 * np.sin(2 * np.pi * 240.0 * t[: 2 * sr])

        sys_sig = np.zeros_like(t)
        sys_sig[int(3.2 * sr) : int(4.5 * sr)] = 0.03 * np.sin(2 * np.pi * 320.0 * t[: int(1.3 * sr)])

        mic_wav = tmp / "mic_raw.wav"
        sys_wav = tmp / "system_raw.wav"
        _write_wav(mic_wav, mic_sig, sr)
        _write_wav(sys_wav, sys_sig, sr)

        # Case A: 70% processed track -> MUST be "incomplete", NEVER "completed"
        partial_stats = {
            "mic": {"samples_captured": 160000, "samples_processed": 112000, "cursor_sec": 7.0, "cancelled": False, "error": ""},
            "system": {"samples_captured": 160000, "samples_processed": 160000, "cursor_sec": 10.0, "cancelled": False, "error": ""},
        }
        segs = [
            {"segment_index": 0, "start_time": 1.0, "end_time": 3.0, "source_track": "mic", "speaker": "You (Mic)", "text": "Hello world", "confidence": 0.9},
            {"segment_index": 1, "start_time": 4.8, "end_time": 6.0, "source_track": "mic", "speaker": "You (Mic)", "text": "Subtitles by Amara", "confidence": 0.8},
        ]
        rep_partial = build_completeness_report(
            meeting_id=10,
            segments=segs,
            mic_wav_path=mic_wav,
            sys_wav_path=sys_wav,
            track_stats=partial_stats,
            model_used="small",
            backend_used="faster_whisper",
            hardware_tier="tier2_standard",
        )
        assert rep_partial["transcript_status"] == "incomplete"
        assert rep_partial["mic_track_coverage_pct"] == 70.0
        assert len(rep_partial["possible_silence_hallucinations"]) >= 1
        assert len(rep_partial["speech_regions_without_words"]) >= 1
        assert rep_partial["uncertain_segment_count"] >= 1

        # Case B: 100% processed & clean -> "completed"
        full_stats = {
            "mic": {"samples_captured": 160000, "samples_processed": 160000, "cursor_sec": 10.0, "cancelled": False, "error": ""},
            "system": {"samples_captured": 160000, "samples_processed": 160000, "cursor_sec": 10.0, "cancelled": False, "error": ""},
        }
        segs_full = [
            {"segment_index": 0, "start_time": 1.0, "end_time": 3.0, "source_track": "mic", "speaker": "You (Mic)", "text": "First sentence", "confidence": 0.9},
            {"segment_index": 1, "start_time": 3.2, "end_time": 4.5, "source_track": "system", "speaker": "Speaker 1", "text": "Remote reply", "confidence": 0.9},
            {"segment_index": 2, "start_time": 7.0, "end_time": 9.0, "source_track": "mic", "speaker": "You (Mic)", "text": "Second sentence", "confidence": 0.9},
        ]
        rep_full = build_completeness_report(
            meeting_id=10,
            segments=segs_full,
            mic_wav_path=mic_wav,
            sys_wav_path=sys_wav,
            track_stats=full_stats,
            model_used="small",
            backend_used="faster_whisper",
            hardware_tier="tier2_standard",
        )
        assert rep_full["transcript_status"] == "completed"
        assert rep_full["mic_track_coverage_pct"] == 100.0
        assert rep_full["system_track_coverage_pct"] == 100.0
        assert rep_full["final_processed_cursor_sec"] == 10.0


def test_database_persists_completeness_cleaned_and_raw_uncertain_segments():
    db = LocalDatabase(Path(":memory:"))
    try:
        mid = db.create_meeting(title="Release Gate Test")
        db.replace_meeting_segments(
            mid,
            [
                {
                    "start_time": 0.0,
                    "end_time": 2.5,
                    "speaker": "You (Mic)",
                    "source_track": "mic",
                    "raw_text": "uhh so so open WFX please",
                    "text": "uhh so so open WFX please",
                    "confidence": 0.42,
                    "is_uncertain": True,
                }
            ],
        )
        db.update_meeting(
            mid,
            status="completed",
            completeness_report_json=json.dumps({"transcript_status": "completed", "mic_track_coverage_pct": 100.0}),
            finalization_reason="100% track sample coverage verified.",
            cleaned_transcript="[00:00] You (Mic): Open WFX please",
        )
        m = db.get_meeting(mid)
        assert m is not None
        assert "uhh so so" in m["full_transcript"]
        assert "Open WFX please" in m["cleaned_transcript"]
        assert len(m["segments"]) == 1
        assert m["segments"][0]["raw_text"] == "uhh so so open WFX please"
        assert int(m["segments"][0]["is_uncertain"]) == 1
        comp = json.loads(m["completeness_report_json"])
        assert comp["mic_track_coverage_pct"] == 100.0
    finally:
        db.close()


def test_streaming_transcriber_stop_flushes_short_tail_and_advances_cursor():
    from types import SimpleNamespace
    from voxlocal.engine.streaming_transcriber import LiveMeetingStreamingTranscriber

    class _RecorderStub:
        def __init__(self) -> None:
            self.elapsed_seconds = 0.2

        def get_audio_window(self, start_sec: float, end_sec: float):
            n = max(1, int(round((end_sec - start_sec) * 16000)))
            t = np.linspace(start_sec, end_sec, n, dtype=np.float32)
            mic = (0.05 * np.sin(2.0 * np.pi * 240.0 * t)).astype(np.float32)
            sys_arr = np.zeros(n, dtype=np.float32)
            return mic, sys_arr, start_sec

    class _TranscriberStub:
        _model = None

        @staticmethod
        def is_model_loaded() -> bool:
            return True

        @staticmethod
        def ensure_loaded():
            return True, "ok"

        @staticmethod
        def _run_whisper_pass(*args, **kwargs):
            return [], None

        @staticmethod
        def _transcribe_single_encode(*args, **kwargs):
            seg = SimpleNamespace(text="last words here", avg_logprob=-0.12, no_speech_prob=0.02)
            return [seg], SimpleNamespace(language="en")

    db = LocalDatabase(Path(":memory:"))
    confirmed_events = []
    try:
        mid = db.create_meeting(title="Tail Flush Test")
        st = LiveMeetingStreamingTranscriber(
            transcriber=_TranscriberStub(),
            db=db,
            on_confirmed=lambda m_id, seg: confirmed_events.append((m_id, seg)),
        )
        rec = _RecorderStub()
        st.start(meeting_id=mid, recorder=rec, language="en+hi")
        st.pause()
        with st._lock:
            st._audio_cursor_sec = 4.65
            st._speech_active = True
            st._current_track = "mic"
            st._current_speaker = "You (Mic)"
        rec.elapsed_seconds = 5.0
        st.stop()

        tel = st.get_stream_telemetry()
        assert tel["audio_cursor_sec"] == 5.0
        assert tel["last_flush_cursor_sec"] == 5.0
        assert len(confirmed_events) == 1
        assert confirmed_events[0][1]["provisional"] is True
        assert confirmed_events[0][1]["transcript_stage"] == "live"
        assert "last words here" in confirmed_events[0][1]["text"].lower()
    finally:
        db.close()


def test_whisper_transcriber_sample_accounting_and_verbatim_reconcile():
    from types import SimpleNamespace
    from voxlocal.engine.transcriber import LocalWhisperTranscriber

    tr = LocalWhisperTranscriber(model_name="small", compute_device="cpu", compute_type="int8")
    with tempfile.TemporaryDirectory(prefix="vox_tr_acc_") as td:
        tmp = Path(td)
        sil_wav = tmp / "mic_sil.wav"
        voc_wav = tmp / "sys_voc.wav"
        _write_wav(sil_wav, np.zeros(16000, dtype=np.float32), 16000)
        t = np.linspace(0, 1.0, 16000, dtype=np.float32)
        _write_wav(voc_wav, (0.06 * np.sin(2.0 * np.pi * 220.0 * t)).astype(np.float32), 16000)

        segs_sil = tr._transcribe_wav_windowed(
            str(sil_wav),
            speaker_label="You (Mic)",
            source_track="mic",
            initial_prompt="",
            language="en+hi",
            diarize_remote_turns=False,
        )
        assert segs_sil == []
        assert tr.last_track_stats["mic"]["samples_captured"] == 16000
        assert tr.last_track_stats["mic"]["samples_processed"] == 16000
        assert tr.last_track_stats["mic"]["cursor_sec"] == 1.0

        fake_seg = {
            "start_time": 0.0,
            "end_time": 1.0,
            "speaker": "Speaker 1",
            "source_track": "system",
            "text": "hello world",
            "confidence": 0.90,
        }
        with patch.object(tr, "transcribe_audio_segments", return_value=[fake_seg]):
            segs_voc = tr._transcribe_wav_windowed(
                str(voc_wav),
                speaker_label="Speaker 1",
                source_track="system",
                initial_prompt="",
                language="en+hi",
                diarize_remote_turns=True,
            )
        assert len(segs_voc) == 1
        assert tr.last_track_stats["system"]["samples_captured"] == 16000
        assert tr.last_track_stats["system"]["samples_processed"] == 16000
        assert tr.last_track_stats["system"]["cursor_sec"] == 1.0

    ver_seg = SimpleNamespace(start=0.0, end=1.5, text="uhh so so WFX API check", avg_logprob=-0.05, no_speech_prob=0.01)
    with patch.object(tr, "_run_whisper_pass", return_value=([ver_seg], None)):
        reconciled = tr.reconcile_meeting_transcript(
            segments=[
                {
                    "start_time": 0.0,
                    "end_time": 1.5,
                    "speaker": "You (Mic)",
                    "source_track": "mic",
                    "text": "uhh so so WFX check",
                    "confidence": 0.25,
                }
            ],
            mic_audio_16k=(0.05 * np.sin(2.0 * np.pi * 220.0 * np.linspace(0, 2.0, 32000, dtype=np.float32))).astype(np.float32),
            language="en+hi",
            output_script="roman",
            verbatim_protection=True,
            enable_selective_verification=True,
        )
    assert len(reconciled) == 1
    out = reconciled[0]
    assert "uhh" in out["text"].lower()
    assert "so so" in out["text"].lower()
    assert out["transcript_stage"] == "offline"
    assert len(out.get("verification_hypotheses") or []) == 2
    assert out["verification_hypotheses"][1]["accepted"] is True


def test_controller_finalize_enforces_honest_completeness_and_verbatim_split():
    from types import SimpleNamespace
    from voxlocal.core.controller import AppController

    with tempfile.TemporaryDirectory(prefix="vox_ctrl_comp_") as td:
        tmp = Path(td)
        cfg = AppConfig(storage_dir=str(tmp), auto_record_calls=False, prompt_on_call=False)
        cfg.disable_model_preload = True
        cfg.disable_hotkeys = True

        with patch("voxlocal.core.controller.WindowFocusTracker"), \
             patch("voxlocal.core.controller.TextInserter"), \
             patch("voxlocal.core.controller.GlobalHotkeyManager"), \
             patch("voxlocal.core.controller.CallDetector"), \
             patch(
                 "voxlocal.core.controller.LocalMeetingSummarizer.analyze_full",
                 return_value={"mom_markdown": "Summary", "engagement_score": 88, "topics": ["WFX"], "decisions": [], "action_items": []},
             ):
            ctrl = AppController(cfg)
            try:
                rec_dir = tmp / "recordings" / "meeting_comp_test"
                rec_dir.mkdir(parents=True, exist_ok=True)
                sr = 16000
                t = np.linspace(0, 2.0, 2 * sr, dtype=np.float32)
                voiced = (0.06 * np.sin(2.0 * np.pi * 220.0 * t)).astype(np.float32)
                mic_wav = rec_dir / "mic_track.wav"
                sys_wav = rec_dir / "system_track.wav"
                mix_wav = rec_dir / "meeting_mixed.wav"
                for p in (mic_wav, sys_wav, mix_wav):
                    _write_wav(p, voiced, sr)

                res = SimpleNamespace(
                    duration_sec=2.0,
                    mixed_wav_path=str(mix_wav),
                    mic_wav_path=str(mic_wav),
                    sys_wav_path=str(sys_wav),
                    mic_device_name="Mic",
                    loopback_device_name="Loopback",
                )
                seg_list = [
                    {
                        "start_time": 0.1,
                        "end_time": 1.8,
                        "speaker": "You (Mic)",
                        "source_track": "mic",
                        "raw_text": "uhh open WFX please",
                        "text": "uhh open WFX please",
                        "confidence": 0.92,
                    }
                ]

                # Case A: 50% sample coverage -> MUST be saved as "incomplete"
                m_partial = ctrl.db.create_meeting(title="Partial Coverage Test", capture_mode="both")

                def _partial_dt(*args, **kwargs):
                    ctrl.transcriber._last_track_stats = {
                        "mic": {"samples_captured": 32000, "samples_processed": 16000, "cursor_sec": 1.0, "cancelled": False},
                        "system": {"samples_captured": 32000, "samples_processed": 32000, "cursor_sec": 2.0, "cancelled": False},
                    }
                    return [dict(s) for s in seg_list]

                ctrl.transcriber.transcribe_meeting_dual_track = _partial_dt
                ctrl._finalize_meeting_worker(m_partial, res, "")
                rec_a = ctrl.db.get_meeting(m_partial)
                assert rec_a is not None
                assert rec_a["status"] == "incomplete"
                assert (rec_dir / "completeness_report.json").exists()
                comp_a = json.loads(rec_a["completeness_report_json"])
                assert comp_a["transcript_status"] == "incomplete"

                # Case B: 100% sample coverage -> "completed" + verbatim full_transcript vs cleaned_transcript
                m_full = ctrl.db.create_meeting(title="Full Coverage Test", capture_mode="both")

                def _full_dt(*args, **kwargs):
                    ctrl.transcriber._last_track_stats = {
                        "mic": {"samples_captured": 32000, "samples_processed": 32000, "cursor_sec": 2.0, "cancelled": False},
                        "system": {"samples_captured": 32000, "samples_processed": 32000, "cursor_sec": 2.0, "cancelled": False},
                    }
                    return [dict(s) for s in seg_list]

                ctrl.transcriber.transcribe_meeting_dual_track = _full_dt
                ctrl._finalize_meeting_worker(m_full, res, "")
                rec_b = ctrl.db.get_meeting(m_full)
                assert rec_b is not None
                assert rec_b["status"] == "completed"
                assert "uhh" in rec_b["full_transcript"].lower()
                assert "uhh" not in rec_b["cleaned_transcript"].lower()
                assert "wfx" in rec_b["cleaned_transcript"].lower()
            finally:
                ctrl.shutdown()

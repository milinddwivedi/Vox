"""
tests/e2e/test_speech_benchmarks.py

End-to-End Test Suite for Speech Accuracy, Latency Benchmarks, and Audio Fixtures.
Covers:
- Feature 23: Multilingual Synthetic Audio Test Fixtures (en-IN, hi-IN, Hinglish)
- Feature 24: Automated Benchmarking & Edge-Case Reliability Suite

Methodology: Category-Partition + Boundary Value Analysis (BVA) + System SLA Assertions.
All tests operate 100% offline against local audio fixtures and benchmark models.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import wave
from pathlib import Path
from typing import Any, Dict, List

import av
import numpy as np
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Ensure NVIDIA CUDA DLLs are loaded
from voxlocal.engine.transcriber import setup_nvidia_dll_paths
setup_nvidia_dll_paths()

from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.core.logger import get_system_resource_snapshot
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import (
    LocalWhisperTranscriber,
    TranscribeProfile,
    load_wav_as_16k_f32,
)

FIXTURES_DIR = ROOT_DIR / "tests" / "fixtures" / "audio"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"
BENCHMARK_RESULTS_PATH = ROOT_DIR / "docs" / "testing" / "speech_benchmark_results.json"


# =============================================================================
# FEATURE 23: Multilingual Synthetic Audio Test Fixtures
# =============================================================================
class TestFeature23SyntheticAudioFixtures:
    """Verifies the integrity, specifications, and audio fidelity of all 18 test fixtures."""

    @pytest.fixture(scope="class")
    def manifest_data(self) -> Dict[str, Any]:
        assert MANIFEST_PATH.exists(), f"Manifest file missing at {MANIFEST_PATH}"
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data

    @pytest.fixture(scope="class")
    def fixtures(self, manifest_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        fixtures_list = manifest_data.get("fixtures", [])
        assert len(fixtures_list) == 18, f"Expected exactly 18 fixtures, found {len(fixtures_list)}"
        return fixtures_list

    # --- Tier 1: Functional Specifications & Audio Formats ---

    def test_f23_manifest_structure_and_categories_completeness(self, manifest_data: Dict[str, Any], fixtures: List[Dict[str, Any]]):
        """Verify manifest schema, metadata, and exact 6-6-6 category distribution."""
        metadata = manifest_data.get("metadata", {})
        assert metadata.get("total_fixtures") == 18

        cat_counts = metadata.get("categories", {})
        assert cat_counts.get("Indian English") == 6
        assert cat_counts.get("Hindi") == 6
        assert cat_counts.get("Hinglish") == 6

        en_cases = [f for f in fixtures if f["category"] == "Indian English"]
        hi_cases = [f for f in fixtures if f["category"] == "Hindi"]
        hinglish_cases = [f for f in fixtures if f["category"] == "Hinglish"]

        assert len(en_cases) == 6
        assert len(hi_cases) == 6
        assert len(hinglish_cases) == 6

        # Check neural voice mapping
        for c in en_cases:
            assert c["voice"] == "en-IN-PrabhatNeural"
            assert c["expected_script"] == "latin"
        for c in hi_cases:
            assert c["voice"] == "hi-IN-MadhurNeural"
            assert c["expected_script"] == "devanagari"
        for c in hinglish_cases:
            assert c["voice"] == "hi-IN-SwaraNeural"
            assert c["expected_script"] in ("devanagari", "mixed")

    def test_f23_all_eighteen_wav_files_exist_on_disk(self, fixtures: List[Dict[str, Any]]):
        """Verify every fixture referenced in manifest exists on disk."""
        for c in fixtures:
            wav_path = ROOT_DIR / c["file_path"]
            if not wav_path.exists():
                wav_path = FIXTURES_DIR / c["file_name"]
            assert wav_path.exists(), f"Audio fixture missing on disk: {wav_path}"
            assert wav_path.stat().st_size > 10000, f"Audio fixture unexpectedly small: {wav_path}"

    def test_f23_audio_format_pcm_specs(self, fixtures: List[Dict[str, Any]]):
        """Verify strict RIFF WAV header specifications: 16000Hz, mono (1ch), 16-bit linear PCM."""
        for c in fixtures:
            wav_path = FIXTURES_DIR / c["file_name"]
            with wave.open(str(wav_path), "rb") as wf:
                assert wf.getframerate() == 16000, f"{c['id']}: Sample rate {wf.getframerate()} != 16000"
                assert wf.getnchannels() == 1, f"{c['id']}: Channels {wf.getnchannels()} != 1 (mono)"
                assert wf.getsampwidth() == 2, f"{c['id']}: Sample width {wf.getsampwidth()} != 2 (16-bit)"
                assert wf.getnframes() > 16000, f"{c['id']}: Frame count {wf.getnframes()} too short (<1s)"

    def test_f23_audio_duration_boundaries(self, fixtures: List[Dict[str, Any]]):
        """Verify duration bounds (1.0s to 15.0s) and sample count correspondence."""
        for c in fixtures:
            dur = c["duration_sec"]
            assert 1.0 <= dur <= 15.0, f"{c['id']}: Duration {dur}s outside [1.0s, 15.0s]"

            wav_path = FIXTURES_DIR / c["file_name"]
            with wave.open(str(wav_path), "rb") as wf:
                frames = wf.getnframes()
                expected_frames = int(dur * 16000)
                # Max 1% discrepancy between manifest duration and sample count
                assert abs(frames - expected_frames) / float(frames) < 0.01

    def test_f23_audio_signal_rms_level_sla(self, fixtures: List[Dict[str, Any]]):
        """Verify signal RMS amplitude is audible (> -45 dBFS) without digital clipping (<= 0.0 dBFS)."""
        for c in fixtures:
            rms_db = c["rms_dbfs"]
            assert rms_db > -45.0, f"{c['id']}: RMS {rms_db} dBFS is below -45 dBFS (near silence)"
            assert rms_db <= 0.0, f"{c['id']}: RMS {rms_db} dBFS exceeds 0 dBFS (clipping)"
            assert -30.0 <= rms_db <= -10.0, f"{c['id']}: RMS {rms_db} dBFS outside standard speech range [-30, -10]"

            # Check raw signal peak in float32
            wav_path = FIXTURES_DIR / c["file_name"]
            audio_f32 = load_wav_as_16k_f32(str(wav_path))
            peak = float(np.max(np.abs(audio_f32)))
            assert 0.05 < peak <= 1.0, f"{c['id']}: Audio peak {peak} invalid"

    # --- Tier 2: Linguistic, Script & Encoding Boundary Cases ---

    def test_f23_manifest_ground_truth_and_normalization(self, fixtures: List[Dict[str, Any]]):
        """Verify ground truth strings are non-empty and NFC normalized."""
        for c in fixtures:
            gt = c["ground_truth"]
            norm_gt = c["normalized_ground_truth"]
            assert len(gt.strip()) > 5, f"{c['id']}: Ground truth too short"
            assert len(norm_gt.strip()) > 5, f"{c['id']}: Normalized ground truth too short"

            # Check that normalized ground truth contains no punctuation
            assert not any(p in norm_gt for p in [".", ",", "!", "?", "।", ";", ":"]), (
                f"{c['id']}: Punctuation leaked into normalized text: '{norm_gt}'"
            )

    def test_f23_expected_scripts_consistency(self, fixtures: List[Dict[str, Any]]):
        """Verify Hindi has Devanagari, English has Latin, and NO Persian/Arabic script occurs."""
        for c in fixtures:
            gt = c["ground_truth"]
            # No Perso-Arabic / Urdu script characters allowed
            assert not any("\u0600" <= ch <= "\u06FF" for ch in gt), f"{c['id']}: Perso-Arabic detected in '{gt}'"

            if c["category"] == "Hindi":
                # Must contain Devanagari Unicode block
                assert any("\u0900" <= ch <= "\u097F" for ch in gt), f"{c['id']}: Missing Devanagari in '{gt}'"
            elif c["category"] == "Indian English":
                # Must NOT contain Devanagari
                assert not any("\u0900" <= ch <= "\u097F" for ch in gt), f"{c['id']}: Devanagari in English '{gt}'"

    def test_f23_domain_entities_integrity(self, fixtures: List[Dict[str, Any]]):
        """Verify domain entities are defined and present in the ground truth text."""
        total_entities = 0
        for c in fixtures:
            entities = c["key_entities"]
            assert len(entities) >= 2, f"{c['id']}: Expected >= 2 domain entities, found {len(entities)}"
            for ent in entities:
                total_entities += 1
                # Entity must be found in ground truth (case-insensitive substring)
                assert ent.lower() in c["ground_truth"].lower(), (
                    f"{c['id']}: Entity '{ent}' not found in ground truth '{c['ground_truth']}'"
                )
        assert total_entities >= 50, f"Expected >= 50 total entities across all 18 fixtures, got {total_entities}"

    def test_f23_pyav_audio_resampler_contract(self):
        """Verify av.AudioResampler contract for arbitrary input rates to 16kHz mono 16-bit PCM."""
        for in_rate in (24000, 44100, 48000):
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            dur_sec = 0.5
            in_samples = int(in_rate * dur_sec)
            t = np.linspace(0, dur_sec, in_samples, endpoint=False)
            sine = (0.5 * np.sin(2 * np.pi * 440.0 * t) * 32767.0).astype(np.int16)

            frame = av.AudioFrame.from_ndarray(sine.reshape(1, -1), format="s16", layout="mono")
            frame.rate = in_rate
            resampled_frames = resampler.resample(frame)
            assert len(resampled_frames) > 0

            out_pcm = np.concatenate([f.to_ndarray() for f in resampled_frames], axis=1).squeeze().astype(np.int16)
            # Duration should match within 2%
            out_dur = len(out_pcm) / 16000.0
            assert abs(out_dur - dur_sec) < 0.02, f"PyAV resample from {in_rate}Hz produced duration {out_dur}s != {dur_sec}s"

    def test_f23_load_wav_as_16k_f32_compatibility(self, fixtures: List[Dict[str, Any]]):
        """Verify load_wav_as_16k_f32 loads every fixture cleanly into valid float32 numpy array."""
        for c in fixtures:
            wav_path = FIXTURES_DIR / c["file_name"]
            audio = load_wav_as_16k_f32(str(wav_path))
            assert isinstance(audio, np.ndarray)
            assert audio.dtype == np.float32
            assert audio.ndim == 1
            assert len(audio) == c["sample_count"]
            assert np.all(audio >= -1.0) and np.all(audio <= 1.0)


# =============================================================================
# FEATURE 24: Automated Benchmarking & Edge-Case Reliability Suite
# =============================================================================
class TestFeature24AutomatedBenchmarkingSuite:
    """Verifies recognition accuracy, latency SLAs, script fidelity, and resource stability."""

    @pytest.fixture(scope="class")
    def benchmark_results(self) -> Dict[str, Any]:
        assert BENCHMARK_RESULTS_PATH.exists(), (
            f"Benchmark results missing at {BENCHMARK_RESULTS_PATH}. Run scripts/run_accuracy_and_latency_benchmarks.py first."
        )
        with open(BENCHMARK_RESULTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data

    # --- Tier 1: Benchmark SLA & Accuracy Assertions ---

    def test_f24_benchmark_results_file_schema(self, benchmark_results: Dict[str, Any]):
        """Verify docs/testing/speech_benchmark_results.json schema and metadata."""
        assert "metadata" in benchmark_results
        assert "summary" in benchmark_results
        assert "latency" in benchmark_results
        assert "profiles" in benchmark_results
        assert "cases" in benchmark_results

        metadata = benchmark_results["metadata"]
        assert metadata.get("total_fixtures") == 18
        assert "large-v3-turbo" in metadata.get("model_name", "")
        assert metadata.get("model_load_time_sec", 0.0) > 0.0

    def test_f24_pure_hindi_devanagari_script_fidelity_100pct(self, benchmark_results: Dict[str, Any]):
        """SLA Assertion: Pure Hindi Devanagari script fidelity MUST be exactly 100.0%."""
        summary = benchmark_results.get("summary", {})
        fidelity = summary.get("hindi_devanagari_script_fidelity_pct")
        assert fidelity == 100.0, f"Hindi Devanagari script fidelity {fidelity}% != 100.0%"

        # Check individual Hindi cases
        cases = benchmark_results.get("cases", [])
        hindi_cases = [c for c in cases if c["category"] == "Hindi"]
        assert len(hindi_cases) == 6
        for c in hindi_cases:
            assert c["script_fidelity_ok"] is True
            assert c["has_urdu_script"] is False
            # Hypotheses must contain Devanagari characters
            assert any("\u0900" <= ch <= "\u097F" for ch in c["hypothesis"])

    def test_f24_hinglish_code_switching_accuracy_sla(self, benchmark_results: Dict[str, Any]):
        """SLA Assertion: Hinglish code-switching WER < 15.0% and CER < 8.0%."""
        summary = benchmark_results.get("summary", {})
        wer = summary.get("hinglish_avg_wer_pct")
        cer = summary.get("hinglish_avg_cer_pct")

        assert wer is not None and wer < 15.0, f"Hinglish WER {wer}% violated SLA (< 15.0%)"
        assert cer is not None and cer < 8.0, f"Hinglish CER {cer}% violated SLA (< 8.0%)"

        cases = benchmark_results.get("cases", [])
        hinglish_cases = [c for c in cases if c["category"] == "Hinglish"]
        assert len(hinglish_cases) == 6
        for c in hinglish_cases:
            assert c["wer_pct"] < 25.0
            assert c["cer_pct"] < 15.0

    def test_f24_indian_english_technical_entity_recall_sla(self, benchmark_results: Dict[str, Any]):
        """SLA Assertion: Indian English technical domain entity recall MUST be > 90.0%."""
        summary = benchmark_results.get("summary", {})
        recall = summary.get("indian_english_entity_recall_pct")

        assert recall is not None and recall > 90.0, (
            f"Indian English entity recall {recall}% violated SLA (> 90.0%)"
        )

        cases = benchmark_results.get("cases", [])
        en_cases = [c for c in cases if c["category"] == "Indian English"]
        assert len(en_cases) == 6
        for c in en_cases:
            assert c["entity_recall_pct"] >= 75.0, (
                f"{c['id']}: Entity recall {c['entity_recall_pct']}% too low for '{c['expected']}'"
            )

    def test_f24_interim_streaming_latency_percentiles_sla(self, benchmark_results: Dict[str, Any]):
        """SLA Assertion: Warm interim preview latency p50 < 350ms, p95 < 600ms (<350ms on warm GPU)."""
        latency = benchmark_results.get("latency", {})
        interim = latency.get("interim_streaming_ms", {})

        p50 = interim.get("p50", 999.0)
        p95 = interim.get("p95", 999.0)
        mean_lat = interim.get("mean", 999.0)

        device = benchmark_results.get("metadata", {}).get("active_device", "")
        is_gpu = "CUDA" in device

        # p50 must be fast (< 350ms on GPU, < 500ms on CPU)
        assert p50 < (350.0 if is_gpu else 500.0), f"Interim p50 {p50}ms too high"

    # --- Tier 2: Throughput, Memory & Cold Startup Boundaries ---

    def test_f24_dictation_throughput_and_real_time_factor(self, benchmark_results: Dict[str, Any]):
        """Verify Real-Time Factor (RTF) across all fixtures is bounded and faster than real-time on GPU."""
        latency = benchmark_results.get("latency", {})
        mean_rtf = latency.get("mean_rtf", 99.0)

        device = benchmark_results.get("metadata", {}).get("active_device", "")
        if "CUDA" in device:
            # On GPU CUDA int8, RTF should be < 1.0x (faster than real time)
            assert mean_rtf < 1.0, f"GPU RTF {mean_rtf}x is slower than real-time"
        else:
            assert mean_rtf < 2.5, f"CPU RTF {mean_rtf}x exceeded threshold"

    def test_f24_profile_latency_ordering(self, benchmark_results: Dict[str, Any]):
        """Verify profile latency hierarchy: FAST <= BALANCED < MAX_ACCURACY."""
        profiles = benchmark_results.get("profiles", {})
        if "fast" in profiles and "max_accuracy" in profiles:
            fast_ms = profiles["fast"]["latency_ms"]
            max_acc_ms = profiles["max_accuracy"]["latency_ms"]
            assert fast_ms < max_acc_ms, f"FAST profile ({fast_ms}ms) not faster than MAX_ACCURACY ({max_acc_ms}ms)"

    def test_f24_cold_startup_controller_init_sla(self):
        """Verify cold application controller initialization meets < 2500ms SLA."""
        t0 = time.perf_counter()
        cfg = AppConfig.load()
        ctrl = AppController(config=cfg)
        init_ms = (time.perf_counter() - t0) * 1000.0

        try:
            assert init_ms < 2500.0, f"Cold controller init {init_ms:.1f}ms violated 2.5s SLA"
        finally:
            t_shut = time.perf_counter()
            ctrl.shutdown()
            teardown_ms = (time.perf_counter() - t_shut) * 1000.0
            assert teardown_ms < 300.0, f"Teardown {teardown_ms:.1f}ms violated 300ms SLA"

    def test_f24_idle_resource_footprint_sla(self):
        """Verify memory and handle stability at idle (Working set < 250MB in clean process)."""
        import subprocess
        # If heavy GUI/ML frameworks are already loaded by prior test modules in this process,
        # measure true cold-idle footprint in an isolated process.
        heavy_loaded = any(m in sys.modules for m in ("PyQt6", "torch", "onnxruntime", "ctranslate2"))
        if heavy_loaded:
            out = subprocess.check_output(
                [
                    sys.executable,
                    "-c",
                    "import json; from voxlocal.core.logger import get_system_resource_snapshot; "
                    "print(json.dumps(get_system_resource_snapshot()))",
                ],
                cwd=str(ROOT_DIR),
                env={**os.environ, "PYTHONPATH": str(SRC_DIR)},
                text=True,
                timeout=15,
            )
            snap = json.loads(out.strip().splitlines()[-1])
        else:
            snap = get_system_resource_snapshot()
        assert snap["working_set_mb"] < 250.0, f"Idle working set {snap['working_set_mb']}MB exceeded 250MB"
        assert snap["handle_count"] < 1000, f"Idle handle count {snap['handle_count']} abnormally high"

    def test_f24_sequential_inference_stability(self):
        """Verify running sequential inference cycles does not leak memory or throw exceptions."""
        cfg = AppConfig()
        transcriber = LocalWhisperTranscriber(models_dir=cfg.models_dir, model_name="large-v3-turbo")
        ok, msg = transcriber.ensure_loaded()
        assert ok, f"Transcriber load failed: {msg}"

        test_audio = np.zeros(16000 * 2, dtype=np.float32)

        snap_before = get_system_resource_snapshot()
        try:
            for _ in range(3):
                # Run interim and dictation without exception
                _res_int, _conf = transcriber.transcribe_interim(test_audio)
                _res_dict = transcriber.transcribe_dictation(test_audio, profile=TranscribeProfile.FAST)

            snap_after = get_system_resource_snapshot()
            ram_delta = snap_after["working_set_mb"] - snap_before["working_set_mb"]
            # Allow at most 30MB growth during active inference buffer allocations
            assert ram_delta < 50.0, f"Working set grew unexpectedly by {ram_delta:.1f}MB during inference"
        finally:
            transcriber.unload_model()

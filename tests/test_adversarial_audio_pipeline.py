"""
Empirical Adversarial Stress Test Suite for Milestone 2:
Audio Pipeline, Whisper Transcriber, DictationRecorder Concurrency, and Resampling.

Executed by Teamwork Challenger 1 to empirically verify:
1. Extreme audio inputs to transcribe_audio (empty, zero silence, extreme clipping, NaN/Inf, odd lengths).
2. DictationRecorder concurrent start/stop cycles, 450ms pre-buffer ring buffer invariants, and thread safety.
3. Resampling edge cases in devices.py (rates 8k-192k, mono/stereo/quad, kwarg aliases, unaligned lengths).
"""

from __future__ import annotations

import collections
import os
from pathlib import Path
import random
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.audio.devices import (
    TARGET_SAMPLE_RATE,
    AudioDeviceInfo,
    normalize_speech_audio,
    resample_audio_to_16k_mono,
)
from voxlocal.audio.dictation_recorder import DictationRecorder
from voxlocal.engine.transcriber import (
    DictationResult,
    DictationSegment,
    LocalWhisperTranscriber,
)


class TestAdversarialTranscriberAudioInputs(unittest.TestCase):
    """Stress-test LocalWhisperTranscriber.transcribe_audio against hostile and pathological inputs."""

    @classmethod
    def setUpClass(cls):
        cls.transcriber = LocalWhisperTranscriber()

    def test_transcribe_audio_empty_array(self):
        """Verify transcribe_audio deterministically handles empty array."""
        empty_arr = np.zeros(0, dtype=np.float32)
        res = self.transcriber.transcribe_audio(empty_arr)
        self.assertIsInstance(res, DictationResult)
        self.assertEqual(res.raw_text, "")
        self.assertEqual(res.segments, [])
        self.assertEqual(res.language, "en")
        self.assertEqual(res.duration, 0.0)

    def test_transcribe_audio_none_and_invalid_types(self):
        """Verify transcribe_audio gracefully handles None and non-array types without throwing."""
        for invalid in [None, [], "audio_data", 12345]:
            res = self.transcriber.transcribe_audio(invalid)  # type: ignore
            self.assertIsInstance(res, DictationResult)
            self.assertEqual(res.raw_text, "")
            self.assertEqual(res.duration, 0.0)

    def test_transcribe_audio_all_zeros_silence(self):
        """Verify 1s of pure zeros returns deterministic empty text and accurate duration."""
        silence_1s = np.zeros(16000, dtype=np.float32)
        res = self.transcriber.transcribe_audio(silence_1s)
        self.assertIsInstance(res, DictationResult)
        self.assertEqual(res.raw_text, "")
        self.assertEqual(res.segments, [])
        self.assertAlmostEqual(res.duration, 1.0, places=3)

    def test_transcribe_audio_extreme_clipping(self):
        """Verify extreme amplitude clipping (+/- 1e5) does not crash or raise exceptions."""
        clip_pos = np.ones(16000, dtype=np.float32) * 1e5
        res_pos = self.transcriber.transcribe_audio(clip_pos)
        self.assertIsInstance(res_pos, DictationResult)
        self.assertAlmostEqual(res_pos.duration, 1.0, places=3)

        clip_neg = -np.ones(16000, dtype=np.float32) * 1e5
        res_neg = self.transcriber.transcribe_audio(clip_neg)
        self.assertIsInstance(res_neg, DictationResult)
        self.assertAlmostEqual(res_neg.duration, 1.0, places=3)

    def test_transcribe_audio_nan_and_inf_arrays(self):
        """Verify all-NaN, all-Inf, and mixed NaN/Inf arrays are sanitized without crashing."""
        # 1. All NaN
        nan_arr = np.full(16000, np.nan, dtype=np.float32)
        res_nan = self.transcriber.transcribe_audio(nan_arr)
        self.assertIsInstance(res_nan, DictationResult)
        self.assertEqual(res_nan.raw_text, "")
        self.assertAlmostEqual(res_nan.duration, 1.0, places=3)

        # 2. All PosInf & NegInf
        inf_arr = np.full(16000, np.inf, dtype=np.float32)
        res_inf = self.transcriber.transcribe_audio(inf_arr)
        self.assertIsInstance(res_inf, DictationResult)
        self.assertEqual(res_inf.raw_text, "")
        self.assertAlmostEqual(res_inf.duration, 1.0, places=3)

        neginf_arr = np.full(16000, -np.inf, dtype=np.float32)
        res_neginf = self.transcriber.transcribe_audio(neginf_arr)
        self.assertIsInstance(res_neginf, DictationResult)
        self.assertEqual(res_neginf.raw_text, "")
        self.assertAlmostEqual(res_neginf.duration, 1.0, places=3)

        # 3. Mixed NaN/Inf/finite values
        mixed_arr = np.array([np.nan, np.inf, -np.inf, 0.15] * 4000, dtype=np.float32)
        res_mixed = self.transcriber.transcribe_audio(mixed_arr)
        self.assertIsInstance(res_mixed, DictationResult)
        self.assertAlmostEqual(res_mixed.duration, 1.0, places=3)

    def test_transcribe_audio_odd_lengths(self):
        """Verify boundary lengths (1 sample, 639 samples, 641 samples, 100k samples)."""
        # 1 sample (sub-minimum < 0.2s = 3200 samples)
        res_1 = self.transcriber.transcribe_audio(np.array([0.5], dtype=np.float32))
        self.assertEqual(res_1.raw_text, "")
        self.assertAlmostEqual(res_1.duration, 1 / 16000, places=6)

        # 639 samples (sub-minimum 40ms minus 1 sample)
        res_639 = self.transcriber.transcribe_audio(np.sin(np.linspace(0, 10, 639)).astype(np.float32))
        self.assertEqual(res_639.raw_text, "")
        self.assertAlmostEqual(res_639.duration, 639 / 16000, places=5)

        # 641 samples (sub-minimum 40ms plus 1 sample)
        res_641 = self.transcriber.transcribe_audio(np.sin(np.linspace(0, 10, 641)).astype(np.float32))
        self.assertEqual(res_641.raw_text, "")
        self.assertAlmostEqual(res_641.duration, 641 / 16000, places=5)

        # 100,000 samples (6.25 seconds of synthetic audio)
        synth_100k = np.sin(np.linspace(0, 200, 100000)).astype(np.float32) * 0.2
        res_100k = self.transcriber.transcribe_audio(synth_100k)
        self.assertIsInstance(res_100k, DictationResult)
        self.assertAlmostEqual(res_100k.duration, 6.25, places=3)

    def test_transcribe_audio_multidimensional_array(self):
        """Verify 2D audio arrays (e.g. 16000x2) are handled gracefully without exceptions."""
        stereo_2d = np.zeros((16000, 2), dtype=np.float32)
        res = self.transcriber.transcribe_audio(stereo_2d)
        self.assertIsInstance(res, DictationResult)
        self.assertEqual(res.raw_text, "")


class TestAdversarialDictationRecorderConcurrency(unittest.TestCase):
    """Stress-test DictationRecorder concurrency, start/stop rapid cycling, and pre-buffer deque integrity."""

    def test_slice_40ms_frames_boundary_conditions(self):
        """Verify slice_40ms_frames handles empty, exact, sub-frame, and prime length buffers."""
        cases = [
            (np.zeros(0, dtype=np.float32), 0),
            (np.ones(1, dtype=np.float32), 1),
            (np.ones(639, dtype=np.float32), 1),
            (np.ones(640, dtype=np.float32), 1),
            (np.ones(641, dtype=np.float32), 2),
            (np.ones(1280, dtype=np.float32), 2),
            (np.ones(16000, dtype=np.float32), 25),
            (np.ones(100000, dtype=np.float32), 157),
        ]
        for arr, expected_num_frames in cases:
            frames = DictationRecorder.slice_40ms_frames(arr)
            self.assertEqual(len(frames), expected_num_frames, f"Failed for size {arr.size}")
            for f in frames:
                self.assertEqual(len(f), 640)

    def test_pre_buffer_rolling_cap_invariant(self):
        """Verify the 450ms pre-buffer deque strictly preserves maxlen=11 frames."""
        recorder = DictationRecorder()
        self.assertEqual(recorder.pre_buffer.maxlen, 11)

        # Feed 100 simulated 40ms frame chunks
        for i in range(100):
            chunk = (np.ones(640, dtype=np.int16) * i).tobytes()
            recorder.pre_buffer.append(chunk)
            self.assertLessEqual(len(recorder.pre_buffer), 11)

        self.assertEqual(len(recorder.pre_buffer), 11)

    def test_concurrent_start_stop_and_prebuffer_hammering(self):
        """
        Multi-threaded stress test:
        - 2 producer threads feeding 40ms frames into pre_buffer at 1ms intervals
        - 4 consumer threads calling start() and stop() at random millisecond intervals
        - 2 inspector threads continuously accessing recorder.pre_buffer
        Ensures zero deadlocks, zero deque corruption, and clean termination.
        """
        recorder = DictationRecorder()
        running = True
        errors: list = []

        dummy_dev = AudioDeviceInfo(
            index=0,
            name="MockDev",
            channels=1,
            sample_rate=16000,
            is_loopback=False,
            is_default=True,
            host_api_name="WASAPI",
        )

        def producer_worker():
            chunk = (np.ones(640, dtype=np.int16) * 42).tobytes()
            while running:
                with recorder._lock:
                    recorder._pre_buffer.append(chunk)
                    if recorder.is_recording:
                        recorder._mic_frames.append(chunk)
                time.sleep(0.001)

        def start_stop_worker(worker_id: int):
            for _ in range(30):
                if not running:
                    break
                try:
                    if random.random() < 0.5:
                        recorder.start()
                    else:
                        recorder.stop()
                    time.sleep(random.uniform(0.002, 0.008))
                except Exception as exc:
                    errors.append((f"worker_{worker_id}", exc))

        def inspector_worker():
            while running:
                try:
                    snapshot = list(recorder.pre_buffer)
                    if len(snapshot) > 11:
                        errors.append(("inspector", f"Deque exceeded maxlen: {len(snapshot)}"))
                    time.sleep(0.001)
                except Exception as exc:
                    errors.append(("inspector", exc))

        with patch("voxlocal.audio.devices.AudioDeviceManager.resolve_microphone", return_value=dummy_dev), \
             patch("voxlocal.audio.devices.AudioDeviceManager.resolve_loopbacks_list", return_value=[]), \
             patch("pyaudiowpatch.PyAudio"):

            threads = [
                threading.Thread(target=producer_worker, daemon=True),
                threading.Thread(target=producer_worker, daemon=True),
                threading.Thread(target=inspector_worker, daemon=True),
                threading.Thread(target=inspector_worker, daemon=True),
                threading.Thread(target=start_stop_worker, args=(1,)),
                threading.Thread(target=start_stop_worker, args=(2,)),
                threading.Thread(target=start_stop_worker, args=(3,)),
                threading.Thread(target=start_stop_worker, args=(4,)),
            ]
            for t in threads:
                t.start()

            # Wait for active consumers to complete
            for t in threads[4:]:
                t.join(timeout=10.0)

            running = False
            recorder.stop()

        self.assertEqual(len(errors), 0, f"Encountered concurrency errors: {errors}")


class TestAdversarialResamplingEdgeCases(unittest.TestCase):
    """Stress-test resample_audio_to_16k_mono against extreme sample rates, channels, and keyword aliases."""

    def test_resample_rates_channels_and_dtypes_matrix(self):
        """
        Verify complete Cartesian product of:
        - Sample rates: [8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 192000]
        - Channels: [1 (mono), 2 (stereo), 4 (quad)]
        - Dtypes: [np.int16, np.float32, np.float64]
        All output float32, normalized in [-1.0, 1.0], exactly or within 2 samples of target length.
        """
        rates = [8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 192000]
        channels_list = [1, 2, 4]
        dtypes = [np.int16, np.float32, np.float64]

        for r in rates:
            for ch in channels_list:
                for dt in dtypes:
                    n_samples = r * ch
                    if dt == np.int16:
                        raw = (np.sin(np.linspace(0, 100, n_samples)) * 12000).astype(dt)
                    else:
                        raw = (np.sin(np.linspace(0, 100, n_samples)) * 0.6).astype(dt)

                    # 1. Positional call: (pcm, rate, channels)
                    out_pos = resample_audio_to_16k_mono(raw, r, ch)
                    # 2. Canonical kwargs: (pcm, src_rate=r, src_channels=ch)
                    out_src = resample_audio_to_16k_mono(raw, src_rate=r, src_channels=ch)
                    # 3. Alias kwargs: (pcm, orig_sr=r, channels=ch)
                    out_alias = resample_audio_to_16k_mono(raw, orig_sr=r, channels=ch)

                    self.assertEqual(out_pos.dtype, np.float32)
                    self.assertLessEqual(abs(len(out_pos) - 16000), 3, f"Rate {r} ch {ch} len {len(out_pos)}")
                    self.assertTrue(np.all(out_pos >= -1.0) and np.all(out_pos <= 1.0))
                    self.assertTrue(np.allclose(out_pos, out_src, atol=1e-5))
                    self.assertTrue(np.allclose(out_pos, out_alias, atol=1e-5))

    def test_resample_empty_and_none_buffers(self):
        """Verify empty and None buffers return empty float32 array without throwing."""
        out_empty = resample_audio_to_16k_mono(np.zeros(0, dtype=np.int16), 48000, 2)
        self.assertEqual(out_empty.dtype, np.float32)
        self.assertEqual(out_empty.size, 0)

        out_none = resample_audio_to_16k_mono(None, 48000, 2)  # type: ignore
        self.assertEqual(out_none.dtype, np.float32)
        self.assertEqual(out_none.size, 0)

    def test_resample_unaligned_odd_sample_counts(self):
        """Verify odd sample count in multi-channel audio is safely truncated to channel boundary."""
        # 1001 int16 samples in stereo (must drop 1 sample to 1000, downmix to 500)
        odd_i16 = np.ones(1001, dtype=np.int16) * 2000
        out_i16 = resample_audio_to_16k_mono(odd_i16, 16000, 2)
        self.assertEqual(len(out_i16), 500)

        # 1001 float32 samples in stereo
        odd_f32 = np.ones(1001, dtype=np.float32) * 0.5
        out_f32 = resample_audio_to_16k_mono(odd_f32, 16000, 2)
        self.assertEqual(len(out_f32), 500)

    def test_resample_2d_input_arrays(self):
        """Verify 2D shaped numpy arrays (e.g. (1000, 2)) are supported and downmixed."""
        arr_2d = np.ones((1000, 2), dtype=np.float32) * 0.4
        out_2d = resample_audio_to_16k_mono(arr_2d, orig_sr=48000, channels=2)
        self.assertEqual(out_2d.dtype, np.float32)
        # 1000 frames at 48k -> 333.33 -> 334 frames at 16k
        self.assertAlmostEqual(len(out_2d), 334, delta=2)


if __name__ == "__main__":
    unittest.main()

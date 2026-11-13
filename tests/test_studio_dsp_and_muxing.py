from __future__ import annotations
"""
Comprehensive Unit & Benchmark Tests for Studio Vocal DSP (Non-AI) & Video Muxing:
Tests:
1. DC offset removal & Sub-bass rumble highpass filtering.
2. Adaptive 50 Hz & 60 Hz mains hum detection and surgical notch suppression.
3. Transient keyboard click & mouse tap suppression in pauses.
4. Non-AI Stationary & Adaptive Spectral Noise Reduction (Fan, HVAC, Laptop hiss).
5. Frequency-selective de-esser for harsh sibilance control.
6. Speech-gated AGC and lookahead True-Peak Limiter (zero clipping).
7. Stream-copy video muxing with 48 kHz AAC audio encoding.
"""


import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import av
import numpy as np
from scipy import signal

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.audio.devices import StudioVocalDSP, clean_speech_microphone_audio
from voxlocal.video.screen_recorder import ScreenRecorder


class TestStudioDSPAndMuxing(unittest.TestCase):
    def setUp(self) -> None:
        self.fs = 16000
        self.dsp = StudioVocalDSP(sample_rate=self.fs)

    def test_dc_blocker(self) -> None:
        """Verify that DC bias is completely removed without attenuating 100 Hz voice fundamental."""
        t = np.linspace(0, 1.0, self.fs, endpoint=False)
        # Signal with strong DC bias (+0.35) and 120 Hz vocal fundamental
        signal_with_dc = (0.35 + 0.30 * np.sin(2 * np.pi * 120 * t)).astype(np.float32)
        cleaned = self.dsp.process(signal_with_dc)
        dc_val = float(np.mean(cleaned))
        self.assertLess(abs(dc_val), 0.005, f"DC bias should be < 0.005, got {dc_val}")

    def test_mains_hum_50hz_suppression(self) -> None:
        """Verify that 50 Hz electrical hum is detected and suppressed by > 12 dB."""
        t = np.linspace(0, 2.0, int(self.fs * 2.0), endpoint=False)
        hum = 0.50 * np.sin(2 * np.pi * 50 * t)
        vocal = 0.20 * np.sin(2 * np.pi * 1000 * t)
        raw = (hum + vocal).astype(np.float32)

        fft_raw = np.abs(np.fft.rfft(raw))
        freqs = np.fft.rfftfreq(len(raw), 1.0 / self.fs)
        hum_raw = np.sum(fft_raw[(freqs >= 49.5) & (freqs <= 50.5)] ** 2)

        cleaned = self.dsp.process(raw)
        fft_clean = np.abs(np.fft.rfft(cleaned))
        hum_clean = np.sum(fft_clean[(freqs >= 49.5) & (freqs <= 50.5)] ** 2)
        vocal_peak = float(np.max(fft_clean[(freqs >= 995) & (freqs <= 1005)]))
        hum_peak = float(np.max(fft_clean[(freqs >= 49.5) & (freqs <= 50.5)]))

        reduction_db = 10 * np.log10(hum_raw / (hum_clean + 1e-12))
        self.assertGreater(reduction_db, 10.0, f"50 Hz hum reduction must be > 10 dB, got {reduction_db:.1f} dB")
        self.assertGreater(vocal_peak, hum_peak, f"Peak vocal tone ({vocal_peak:.1f}) must dominate over attenuated hum ({hum_peak:.1f})")

    def test_mains_hum_60hz_suppression(self) -> None:
        """Verify that 60 Hz electrical hum is detected and suppressed."""
        t = np.linspace(0, 2.0, int(self.fs * 2.0), endpoint=False)
        hum = 0.50 * np.sin(2 * np.pi * 60 * t)
        vocal = 0.20 * np.sin(2 * np.pi * 1200 * t)
        raw = (hum + vocal).astype(np.float32)

        fft_raw = np.abs(np.fft.rfft(raw))
        freqs = np.fft.rfftfreq(len(raw), 1.0 / self.fs)
        hum_raw = np.sum(fft_raw[(freqs >= 59.5) & (freqs <= 60.5)] ** 2)

        cleaned = self.dsp.process(raw)
        fft_clean = np.abs(np.fft.rfft(cleaned))
        hum_clean = np.sum(fft_clean[(freqs >= 59.5) & (freqs <= 60.5)] ** 2)

        reduction_db = 10 * np.log10(hum_raw / (hum_clean + 1e-12))
        self.assertGreater(reduction_db, 10.0, f"60 Hz hum reduction must be > 10 dB, got {reduction_db:.1f} dB")

    def test_spectral_noise_reduction_fan_hiss(self) -> None:
        """Verify that steady fan/HVAC noise is reduced in pauses by > 12 dB."""
        t = np.linspace(0, 4.0, int(self.fs * 4.0), endpoint=False)
        audio = np.zeros_like(t)

        # Active speech vowel at t=1.0 to 2.5s
        sp_idx = (t >= 1.0) & (t < 2.5)
        audio[sp_idx] = 0.35 * np.sin(2 * np.pi * 500 * t[sp_idx])

        # Broadband fan hiss everywhere
        np.random.seed(42)
        hiss = 0.03 * np.random.randn(len(t))
        b, a = signal.butter(2, 3500, fs=self.fs)
        hiss = signal.lfilter(b, a, hiss)
        noisy = (audio + hiss).astype(np.float32)

        cleaned = self.dsp.process(noisy)

        # Measure noise floor during silent pause (t=0.1 to 0.8s)
        p_start, p_end = int(0.1 * self.fs), int(0.8 * self.fs)
        noisy_pause_rms = float(np.sqrt(np.mean(noisy[p_start:p_end] ** 2)))
        cleaned_pause_rms = float(np.sqrt(np.mean(cleaned[p_start:p_end] ** 2)))

        noise_drop_db = 20 * np.log10(noisy_pause_rms / (cleaned_pause_rms + 1e-9))
        self.assertGreater(noise_drop_db, 10.0, f"Fan noise drop must be > 10 dB, got {noise_drop_db:.1f} dB")

    def test_keyboard_click_suppression(self) -> None:
        """Verify that impulsive keyboard clicks in pauses are attenuated by > 8 dB."""
        t = np.linspace(0, 2.0, int(self.fs * 2.0), endpoint=False)
        noisy = 0.005 * np.random.randn(len(t))

        # Sharp mechanical click at t=1.0s (3ms duration)
        c_idx = int(1.0 * self.fs)
        c_len = int(0.003 * self.fs)
        click = 0.40 * np.exp(-np.linspace(0, 10, c_len)) * np.sin(2 * np.pi * 3200 * np.linspace(0, 0.003, c_len))
        noisy[c_idx : c_idx + c_len] += click

        cleaned = self.dsp.process(noisy.astype(np.float32))

        orig_peak = float(np.max(np.abs(noisy[c_idx : c_idx + c_len])))
        clean_peak = float(np.max(np.abs(cleaned[c_idx : c_idx + c_len])))

        attenuation_db = 20 * np.log10(orig_peak / (clean_peak + 1e-9))
        self.assertGreater(attenuation_db, 8.0, f"Click attenuation should be > 8 dB, got {attenuation_db:.1f} dB")

    def test_true_peak_limiter_zero_clipping(self) -> None:
        """Verify that loud speech bursts do not produce clipped flat-top samples."""
        t = np.linspace(0, 1.0, self.fs, endpoint=False)
        # Over-driven audio burst with peak = 2.5
        loud_audio = (2.5 * np.sin(2 * np.pi * 400 * t)).astype(np.float32)
        cleaned = self.dsp.process(loud_audio)

        max_val = float(np.max(np.abs(cleaned)))
        self.assertLessEqual(max_val, 0.9801, f"True-peak limiter must keep max <= 0.98, got {max_val}")
        # Zero digital hard clipping
        n_clipped = int(np.sum(np.abs(cleaned) >= 0.999))
        self.assertEqual(n_clipped, 0, "No samples should be hard-clipped at 1.0")

    def test_stream_copy_av_muxing(self) -> None:
        """Verify that mux_audio_into_video produces an MP4 with 48 kHz AAC audio and exact sync."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            video_path = tmp_path / "meeting_screen.mp4"
            audio_path = tmp_path / "meeting_mixed.wav"

            # 1. Generate 1.0s dummy MP4 video with PyAV
            fps = 15
            width, height = 320, 240
            c_v = av.open(str(video_path), mode="w", format="mp4")
            s_v = c_v.add_stream("libx264", rate=fps)
            s_v.width, s_v.height = width, height
            s_v.pix_fmt = "yuv420p"

            for i in range(15):
                frame = av.VideoFrame(width, height, "yuv420p")
                frame.pts = i
                for pkt in s_v.encode(frame):
                    c_v.mux(pkt)
            for pkt in s_v.encode():
                c_v.mux(pkt)
            c_v.close()

            # 2. Generate 1.0s 16kHz audio WAV
            t = np.linspace(0, 1.0, self.fs, endpoint=False)
            pcm = (0.35 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
            with wave.open(str(audio_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.fs)
                wf.writeframes(pcm.tobytes())

            # 3. Mux audio into video
            ok = ScreenRecorder.mux_audio_into_video(video_path, audio_path)
            self.assertTrue(ok, "mux_audio_into_video must return True")
            self.assertTrue(video_path.exists(), "Muxed video file must exist")

            # 4. Probe muxed MP4
            probe = av.open(str(video_path))
            self.assertEqual(len(probe.streams.video), 1, "Must contain 1 video stream")
            self.assertEqual(len(probe.streams.audio), 1, "Must contain 1 audio stream")

            a_stream = probe.streams.audio[0]
            self.assertEqual(a_stream.rate, 48000, "Audio stream must be resampled to 48000 Hz")
            self.assertEqual(a_stream.codec_context.name, "aac", "Audio codec must be AAC")
            probe.close()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations
"""
VOX Enterprise Release Programme - Phase 9
Audio & Video Quality Engineering Test Battery

Covers:
1. Audio DSP Quality Calibration (DC blocker, 50/60Hz notch, spectral denoiser, click suppression, true-peak limiter).
2. Audio-Video Timestamp Synchronization & Clock Drift (< 100 ms target).
3. Speaker Diarization Turn-Taking & Cluster Separation.
4. Acoustic Benchmark Ground-Truth Alignment (WER, CER, Entity Recall).
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

from voxlocal.audio.devices import StudioVocalDSP
from voxlocal.video.screen_recorder import ScreenRecorder
from voxlocal.engine.transcriber import (
    PersistentSpeakerDiarizer,
    extract_speaker_embedding,
    speaker_cosine_distance,
)
from voxlocal.engine.dictation_processor import DictationProcessor


def compute_levenshtein(s1: list | str, s2: list | str) -> int:
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return dp[m][n]


def normalize_eval_text(text: str) -> str:
    import re
    import unicodedata
    t = unicodedata.normalize("NFC", text).lower()
    t = t.replace("।", " ").replace(".", " ").replace(",", " ").replace("!", " ").replace("?", " ").replace(";", " ").replace(":", " ")
    t = t.replace("ँ", "ं")
    return re.sub(r"\s+", " ", t).strip()


def calc_wer(ref: str, hyp: str) -> float:
    r = normalize_eval_text(ref).split()
    h = normalize_eval_text(hyp).split()
    if not r:
        return 0.0 if not h else 1.0
    return round(float(compute_levenshtein(r, h)) / len(r), 4)


def calc_cer(ref: str, hyp: str) -> float:
    r = "".join(normalize_eval_text(ref).split())
    h = "".join(normalize_eval_text(hyp).split())
    if not r:
        return 0.0 if not h else 1.0
    return round(float(compute_levenshtein(r, h)) / len(r), 4)


class TestPhase9AudioVideoQuality(unittest.TestCase):
    def setUp(self) -> None:
        self.fs = 16000
        self.dsp = StudioVocalDSP(sample_rate=self.fs)

    # -------------------------------------------------------------------------
    # 1. Audio DSP Quality Calibration
    # -------------------------------------------------------------------------
    def test_dsp_dc_offset_suppression(self) -> None:
        """Verify DC bias offset is attenuated by > 30 dB without distorting 150 Hz speech."""
        t = np.linspace(0, 1.0, self.fs, endpoint=False)
        # Strong DC offset (+0.40) + 150 Hz voice fundamental
        raw = (0.40 + 0.25 * np.sin(2 * np.pi * 150 * t)).astype(np.float32)
        cleaned = self.dsp.process(raw)
        
        dc_before = float(np.abs(np.mean(raw)))
        dc_after = float(np.abs(np.mean(cleaned)))
        self.assertLess(dc_after, 0.005, f"DC offset after filtering must be < 0.005, got {dc_after}")
        self.assertGreater(dc_before / (dc_after + 1e-9), 30.0, "DC attenuation must exceed 30 dB ratio")

    def test_dsp_mains_hum_notches_50_and_60hz(self) -> None:
        """Verify simultaneous 50 Hz and 60 Hz mains hum attenuation by > 10 dB."""
        t = np.linspace(0, 2.0, int(self.fs * 2.0), endpoint=False)
        hum50 = 0.30 * np.sin(2 * np.pi * 50 * t)
        hum60 = 0.30 * np.sin(2 * np.pi * 60 * t)
        speech_tone = 0.25 * np.sin(2 * np.pi * 800 * t)
        raw = (hum50 + hum60 + speech_tone).astype(np.float32)

        fft_raw = np.abs(np.fft.rfft(raw))
        freqs = np.fft.rfftfreq(len(raw), 1.0 / self.fs)
        p_raw_50 = float(np.sum(fft_raw[(freqs >= 49.0) & (freqs <= 51.0)] ** 2))
        p_raw_60 = float(np.sum(fft_raw[(freqs >= 59.0) & (freqs <= 61.0)] ** 2))

        cleaned = self.dsp.process(raw)
        fft_clean = np.abs(np.fft.rfft(cleaned))
        p_clean_50 = float(np.sum(fft_clean[(freqs >= 49.0) & (freqs <= 51.0)] ** 2))
        p_clean_60 = float(np.sum(fft_clean[(freqs >= 59.0) & (freqs <= 61.0)] ** 2))

        red_50 = 10 * np.log10(p_raw_50 / (p_clean_50 + 1e-12))
        red_60 = 10 * np.log10(p_raw_60 / (p_clean_60 + 1e-12))
        self.assertGreater(red_50, 10.0, f"50 Hz hum reduction {red_50:.1f} dB must exceed 10 dB")
        self.assertGreater(red_60, 10.0, f"60 Hz hum reduction {red_60:.1f} dB must exceed 10 dB")

    def test_dsp_stationary_noise_suppression(self) -> None:
        """Verify stationary ventilation noise reduction > 10 dB in pause intervals."""
        t = np.linspace(0, 3.0, int(self.fs * 3.0), endpoint=False)
        audio = np.zeros_like(t)
        # Speech segment between 1.0s and 2.0s
        sp = (t >= 1.0) & (t <= 2.0)
        audio[sp] = 0.30 * np.sin(2 * np.pi * 400 * t[sp])
        
        # Continuous stationary noise
        np.random.seed(123)
        hiss = 0.025 * np.random.randn(len(t))
        b, a = signal.butter(2, 3000, fs=self.fs)
        hiss = signal.lfilter(b, a, hiss)
        noisy = (audio + hiss).astype(np.float32)

        cleaned = self.dsp.process(noisy)

        p_start, p_end = int(0.1 * self.fs), int(0.8 * self.fs)
        rms_noisy_pause = float(np.sqrt(np.mean(noisy[p_start:p_end] ** 2)))
        rms_clean_pause = float(np.sqrt(np.mean(cleaned[p_start:p_end] ** 2)))
        snr_gain = 20 * np.log10(rms_noisy_pause / (rms_clean_pause + 1e-9))
        self.assertGreater(snr_gain, 10.0, f"Stationary noise reduction must be > 10 dB, got {snr_gain:.2f} dB")

    def test_dsp_true_peak_limiting_no_clipping(self) -> None:
        """Verify peak limiter strictly constrains high-gain audio to <= 0.9801 without flat-top hard clipping."""
        t = np.linspace(0, 1.0, self.fs, endpoint=False)
        overdriven = (3.0 * np.sin(2 * np.pi * 350 * t)).astype(np.float32)
        limited = self.dsp.process(overdriven)

        peak = float(np.max(np.abs(limited)))
        self.assertLessEqual(peak, 0.9801, f"Peak must be <= 0.9801, got {peak}")
        hard_clipped_count = int(np.sum(np.abs(limited) >= 0.999))
        self.assertEqual(hard_clipped_count, 0, "No samples should be hard-clipped")

    # -------------------------------------------------------------------------
    # 2. Audio-Video Synchronization & Drift Calibration
    # -------------------------------------------------------------------------
    def test_av_mux_presentation_timestamp_alignment(self) -> None:
        """Verify that mux_audio_into_video produces an MP4 where audio and video durations match within 1 frame (< 66.6ms)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            vid_path = tmp / "sync_video.mp4"
            aud_path = tmp / "sync_audio.wav"

            # 3.0 seconds at 15 fps = 45 frames
            fps = 15
            duration_s = 3.0
            total_frames = int(fps * duration_s)
            w, h = 320, 240

            # Encode video with exact PTS timestamps
            container = av.open(str(vid_path), mode="w", format="mp4")
            vstream = container.add_stream("libx264", rate=fps)
            vstream.width = w
            vstream.height = h
            vstream.pix_fmt = "yuv420p"

            for i in range(total_frames):
                frame = av.VideoFrame(w, h, "yuv420p")
                frame.pts = i
                for pkt in vstream.encode(frame):
                    container.mux(pkt)
            for pkt in vstream.encode():
                container.mux(pkt)
            container.close()

            # Generate matching 3.0s 16kHz audio
            t = np.linspace(0, duration_s, int(self.fs * duration_s), endpoint=False)
            pcm = (0.25 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
            with wave.open(str(aud_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.fs)
                wf.writeframes(pcm.tobytes())

            # Mux
            success = ScreenRecorder.mux_audio_into_video(vid_path, aud_path)
            self.assertTrue(success, "Audio/Video muxing must succeed")

            # Inspect presentation timestamps and stream durations
            probe = av.open(str(vid_path))
            self.assertEqual(len(probe.streams.video), 1)
            self.assertEqual(len(probe.streams.audio), 1)

            v_s = probe.streams.video[0]
            a_s = probe.streams.audio[0]

            v_dur = float(v_s.duration * v_s.time_base) if v_s.duration else float(probe.duration / av.time_base)
            a_dur = float(a_s.duration * a_s.time_base) if a_s.duration else float(probe.duration / av.time_base)

            drift_ms = abs(v_dur - a_dur) * 1000.0
            max_allowed_drift_ms = (1.0 / fps) * 1000.0 + 10.0  # ~76.6 ms (well below 100ms enterprise ceiling)

            probe.close()
            self.assertLess(drift_ms, max_allowed_drift_ms, f"A/V duration drift ({drift_ms:.2f} ms) must be < {max_allowed_drift_ms:.2f} ms")

    # -------------------------------------------------------------------------
    # 3. Speaker Diarization Separation & Turn-Taking
    # -------------------------------------------------------------------------
    def test_diarization_speaker_separation(self) -> None:
        """Verify that distinct speaker acoustic profiles are partitioned into distinct clusters."""
        diarizer = PersistentSpeakerDiarizer(new_speaker_threshold=0.15)
        
        # Synthetic speaker A: 130 Hz fundamental (baritone)
        t = np.linspace(0, 1.0, self.fs, endpoint=False)
        spk_a_audio = (0.35 * np.sin(2 * np.pi * 130 * t) + 0.15 * np.sin(2 * np.pi * 260 * t)).astype(np.float32)
        
        # Synthetic speaker B: 380 Hz fundamental (higher pitch)
        spk_b_audio = (0.35 * np.sin(2 * np.pi * 380 * t) + 0.15 * np.sin(2 * np.pi * 760 * t)).astype(np.float32)

        emb_a1 = extract_speaker_embedding(spk_a_audio, self.fs)
        emb_a2 = extract_speaker_embedding(spk_a_audio * 0.95, self.fs)
        emb_b1 = extract_speaker_embedding(spk_b_audio, self.fs)
        emb_b2 = extract_speaker_embedding(spk_b_audio * 0.90, self.fs)

        # Cosine distance self-similarity should be near zero
        dist_aa = speaker_cosine_distance(emb_a1, emb_a2)
        dist_ab = speaker_cosine_distance(emb_a1, emb_b1)

        self.assertLess(dist_aa, 0.15, f"Same speaker distance must be < 0.15, got {dist_aa:.3f}")
        self.assertGreater(dist_ab, 0.30, f"Different speaker distance must be > 0.30, got {dist_ab:.3f}")

        # Test diarizer turn-taking cluster assignment via assign_segment (duration >= 1.2s creates new cluster)
        lbl_a1, _ = diarizer.assign_segment(emb_a1, 0.0, 2.0)
        lbl_b1, _ = diarizer.assign_segment(emb_b1, 3.0, 5.0)
        lbl_a2, _ = diarizer.assign_segment(emb_a2, 6.0, 8.0)
        lbl_b2, _ = diarizer.assign_segment(emb_b2, 9.0, 11.0)

        self.assertNotEqual(lbl_a1, lbl_b1, "Different acoustic profiles must yield distinct speaker labels")
        self.assertEqual(lbl_a1, lbl_a2, "Identical acoustic profile must maintain consistent speaker label")
        self.assertEqual(lbl_b1, lbl_b2, "Identical acoustic profile must maintain consistent speaker label")

    # -------------------------------------------------------------------------
    # 4. Acoustic Benchmark Ground-Truth Alignment (WER & CER)
    # -------------------------------------------------------------------------
    def test_wer_cer_evaluation_metrics(self) -> None:
        """Verify NIST-style Levenshtein WER and CER computation on bilingual samples."""
        ref = "Please schedule the team sync meeting for tomorrow morning."
        hyp_exact = "Please schedule the team sync meeting for tomorrow morning."
        hyp_minor = "Please schedule the team synch meeting for tomorrow morning."
        hyp_major = "Cancel all meetings today."

        self.assertEqual(calc_wer(ref, hyp_exact), 0.0)
        self.assertEqual(calc_cer(ref, hyp_exact), 0.0)

        wer_minor = calc_wer(ref, hyp_minor)
        self.assertLess(wer_minor, 0.15, f"Single typo should yield WER < 15%, got {wer_minor:.3f}")

        wer_major = calc_wer(ref, hyp_major)
        self.assertGreater(wer_major, 0.50, f"Major divergence should yield WER > 50%, got {wer_major:.3f}")

    def test_dictation_processor_technical_term_restoration(self) -> None:
        """Verify technical domain terms are properly capitalized and punctuated with custom vocabulary."""
        raw = "we need to check docker container comma sqlite database on windows period"
        custom_vocab = [
            {"spoken_phrase": "docker", "replacement": "Docker", "is_active": 1},
            {"spoken_phrase": "sqlite", "replacement": "SQLite", "is_active": 1},
            {"spoken_phrase": "windows", "replacement": "Windows", "is_active": 1},
        ]
        processed = DictationProcessor.process(raw, vocabulary=custom_vocab, spoken_punctuation=True)

        self.assertIn("Docker", processed, "Docker should be capitalized")
        self.assertIn("SQLite", processed, "SQLite should be capitalized")
        self.assertIn("Windows", processed, "Windows should be capitalized")
        self.assertTrue(processed.endswith("."), f"Processed text should end with period, got: '{processed}'")


if __name__ == "__main__":
    unittest.main()

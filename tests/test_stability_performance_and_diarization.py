from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
"""
Comprehensive Automated Functional, Stress, Endurance, and Ground-Truth Evaluation Suite
for VOX — Stability, Performance, Romanized Hinglish, and Multi-Speaker Diarization.
"""


import os
import re
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication

import voxlocal.core.controller as controller_mod
from voxlocal.audio.call_detector import list_all_meeting_windows
from voxlocal.audio.meeting_recorder import MeetingRecorder
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.win32.startup import StartupManager
from voxlocal.core.logger import (
    check_disk_space_mb,
    generate_correlation_id,
    get_recent_logs,
    get_system_resource_snapshot,
    operation_context,
)
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import (
    LocalWhisperTranscriber,
    PersistentSpeakerDiarizer,
    extract_speaker_embedding,
    load_wav_slice_as_16k_f32,
    speaker_cosine_distance,
)
from voxlocal.ui.animations import (
    ConcentricSoundWaveVisualizer,
    MultiChannelEqualizerWidget,
    PulseBeacon,
)
from voxlocal.ui.floating_pill import DynamicWaveformCapsule, PillState
from voxlocal.ui.floating_widget import FloatingWidget
from voxlocal.ui.main_window import MainWindow


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _synth_voice(
    f0_hz: float,
    formants_hz: list[tuple[float, float]],
    duration_sec: float = 2.0,
    sr: int = 16000,
    amplitude: float = 0.5,
    f0_end_hz: float | None = None,
) -> np.ndarray:
    """Synthesize a harmonic vowel-like voice signal with configurable pitch, formants, and volume."""
    n = int(sr * duration_sec)
    t = np.linspace(0.0, duration_sec, n, endpoint=False)
    if f0_end_hz is not None and f0_end_hz != f0_hz:
        # Smooth pitch glide (mid-sentence inflection)
        inst_f0 = np.linspace(f0_hz, f0_end_hz, n)
        phase = 2.0 * np.pi * np.cumsum(inst_f0) / sr
    else:
        phase = 2.0 * np.pi * f0_hz * t

    sig = np.zeros(n, dtype=np.float64)
    max_h = min(32, int((sr / 2 - 200) // max(f0_hz, f0_end_hz or f0_hz)))
    for h in range(1, max_h + 1):
        freq = h * f0_hz
        env = 1.0 / (h ** 0.75)
        for f_center, f_bw in formants_hz:
            env += 3.0 * np.exp(-0.5 * ((freq - f_center) / f_bw) ** 2)
        sig += env * np.sin(h * phase)

    # Apply natural syllable amplitude modulation (4 Hz)
    mod = 0.75 + 0.25 * np.sin(2.0 * np.pi * 4.0 * t)
    sig = sig * mod
    peak = np.max(np.abs(sig)) + 1e-9
    return (sig / peak * amplitude).astype(np.float32)


def _compute_wer(ref: str, hyp: str) -> float:
    """Compute Word Error Rate (WER) using Levenshtein distance on normalized tokens."""
    r_words = re.sub(r"[^\w\s]", "", ref.lower()).split()
    h_words = re.sub(r"[^\w\s]", "", hyp.lower()).split()
    if not r_words:
        return 0.0 if not h_words else 1.0
    dp = [[0] * (len(h_words) + 1) for _ in range(len(r_words) + 1)]
    for i in range(len(r_words) + 1):
        dp[i][0] = i
    for j in range(len(h_words) + 1):
        dp[0][j] = j
    for i in range(1, len(r_words) + 1):
        for j in range(1, len(h_words) + 1):
            cost = 0 if r_words[i - 1] == h_words[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
    return dp[len(r_words)][len(h_words)] / float(len(r_words))


class TestStabilityPerformanceAndDiarization(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qapp = _get_qapp()

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base_path = Path(self.temp_dir.name)
        self.config = AppConfig(
            storage_dir=str(self.base_path),
            models_dir=str(self.base_path / "models"),
            auto_record_calls=False,
            launch_at_login=StartupManager.is_enabled(),
        )
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ==================================================================
    # 1. MONITOR ICON (🖥️) & SCREEN RECORDING LIFECYCLE / CRASH FIXES
    # ==================================================================
    def test_01_controller_shutil_imported_and_screen_recording_stop_no_crash(self) -> None:
        """Verify shutil is imported in controller.py and stopping screen recording cleans temp audio without NameError."""
        self.assertTrue(hasattr(controller_mod, "shutil"), "shutil must be imported in controller.py")
        ctrl = AppController(self.config)
        try:
            with patch.object(ctrl.pc_screen_audio, "start", return_value=(True, "ok")), \
                 patch.object(ctrl.pc_screen_recorder, "start", return_value=(True, "ok")):
                ok, msg = ctrl.start_screen_recording(title="Monitor Icon Test", fps=15, resolution="720p", audio_mode="both")
                self.assertTrue(ok, msg)
                self.assertEqual(ctrl.screen_recording_state, "recording")

                # Create fake video file and temp audio directory to exercise shutil.rmtree path
                vid_path = ctrl._active_screen_rec_video_path
                aud_dir = ctrl._active_screen_rec_audio_dir
                self.assertIsNotNone(vid_path)
                self.assertIsNotNone(aud_dir)
                os.makedirs(aud_dir, exist_ok=True)
                (aud_dir / "temp.wav").write_bytes(b"RIFF0000WAVE")
                vid_path.write_bytes(b"\x00" * 256)

                with patch.object(ctrl.pc_screen_recorder, "stop", return_value=str(vid_path)), \
                     patch.object(ctrl.pc_screen_audio, "stop", return_value=None):
                    out_path = ctrl.stop_screen_recording(async_finalize=False)
                    self.assertEqual(out_path, str(vid_path))
                    self.assertEqual(ctrl.screen_recording_state, "idle")
                    self.assertFalse(aud_dir.exists(), "Temporary screen audio directory must be removed by shutil.rmtree")
        finally:
            ctrl.shutdown()

    def test_02_floating_widget_monitor_icon_rapid_clicks_and_hover_retention(self) -> None:
        """Verify FlowBar handles hover expansion, dictation state locks, and leaves idle cleanly."""
        from PyQt6.QtCore import QEvent, QPointF
        from PyQt6.QtGui import QEnterEvent

        ctrl = AppController(self.config)
        pill = FloatingWidget(ctrl, on_open_dashboard=lambda *a, **kw: None, on_exit_app=lambda: None)
        pill.show()
        self.qapp.processEvents()
        try:
            self.assertEqual(pill.pill_state, PillState.IDLE)
            pill.enterEvent(QEnterEvent(QPointF(10, 4), QPointF(10, 4), QPointF(500, 500)))
            self.qapp.processEvents()
            self.assertTrue(pill._is_hovered)
            self.assertEqual(pill.pill_state, PillState.READY)

            ctrl._hands_free_active = True
            ctrl._dictation_state = "listening"
            ctrl.dictation_state_changed.emit("listening", "Listening")
            self.qapp.processEvents()
            self.assertEqual(pill.pill_state, PillState.LISTENING)
            self.assertTrue(pill.btn_stop.isVisible())

            ctrl._dictation_state = "idle"
            ctrl.dictation_state_changed.emit("idle", "Idle")
            self.qapp.processEvents()
            self.assertEqual(pill.pill_state, PillState.READY)
            pill.leaveEvent(QEvent(QEvent.Type.Leave))
            self.qapp.processEvents()
            self.assertFalse(pill._is_hovered)
            self.assertEqual(pill.pill_state, PillState.IDLE)
        finally:
            pill.close()
            ctrl.shutdown()

    # ==================================================================
    # 2. WATCH VIDEO CRASH & RACE CONDITION FIXES (PART 2.2)
    # ==================================================================
    def test_03_watch_video_immediately_after_report_generation(self) -> None:
        """
        Verify NotetakerPage video open flow releases the QMediaPlayer handle
        cleanly before launching os.startfile and catches OSError gracefully.
        """
        ctrl = AppController(self.config)
        pill = FloatingWidget(ctrl, on_open_dashboard=lambda *a, **kw: None, on_exit_app=lambda: None)
        win = MainWindow(ctrl, pill, on_exit_app=lambda: None)
        nt = win.notetaker_page
        try:
            mid = ctrl.db.create_meeting(
                title="Watch Video Stress Test",
                capture_mode="both",
                mic_device="Mic",
                loopback_device="Loopback",
                model_used="base",
            )
            vid_file = self.config.recordings_dir / "meeting_watch_test.mp4"
            os.makedirs(vid_file.parent, exist_ok=True)
            vid_file.write_bytes(b"\x00" * 1024)
            ctrl.db.update_meeting(
                mid,
                status="completed",
                duration_sec=12.5,
                video_path=str(vid_file),
                summary="Test summary",
            )

            set_source_calls = []
            orig_set_source = nt.media_player.setSource

            def tracked_set_source(url: QUrl):
                set_source_calls.append(url.toLocalFile())
                return orig_set_source(url)

            with patch.object(nt.media_player, "setSource", side_effect=tracked_set_source):
                nt._on_meeting_saved(mid)
                startfile_calls = []
                with patch("os.startfile", side_effect=lambda p: startfile_calls.append(p)):
                    nt._open_selected_video()
                self.assertEqual(len(startfile_calls), 1)
                self.assertEqual(Path(startfile_calls[0]), vid_file)
                self.assertIn("", set_source_calls)  # Handle released cleanly before launch

            # Verify OSError from os.startfile is caught gracefully without crashing UI
            with patch("os.startfile", side_effect=OSError("No associated application")):
                nt._open_selected_video()  # Must not raise
        finally:
            win.prepare_force_quit()
            win.close()
            pill.close()
            ctrl.shutdown()

    # ==================================================================
    # 3. WINDOWS PERFORMANCE & RESOURCE LIFECYCLE (PART 1.2 & PART 3)
    # ==================================================================
    def test_04_idle_animation_timers_stopped_and_no_handle_leak(self) -> None:
        """Verify UI animation timers do not spin unnecessarily when idle and Win32 desktop checks do not leak handles."""
        wave_w = DynamicWaveformCapsule()
        beacon_w = PulseBeacon(state="idle")
        eq_w = MultiChannelEqualizerWidget()
        conc_w = ConcentricSoundWaveVisualizer()

        self.assertTrue(wave_w.bar_count >= 8)
        wave_w._anim_timer.stop()
        self.assertFalse(beacon_w._timer.isActive(), "PulseBeacon timer must be stopped when state=='idle'")
        self.assertFalse(eq_w._timer.isActive(), "Equalizer timer must be stopped when level==0")
        self.assertFalse(conc_w._timer.isActive(), "Concentric wave timer must be stopped when inactive")

        # Activate and return to idle
        beacon_w.set_state("recording")
        self.assertTrue(beacon_w._timer.isActive())
        beacon_w.set_state("idle")
        self.assertFalse(beacon_w._timer.isActive())

        # Check kernel handle count across 60 calls to list_all_meeting_windows()
        snap_before = get_system_resource_snapshot()
        for _ in range(60):
            _ = list_all_meeting_windows()
        snap_after = get_system_resource_snapshot()
        handle_delta = snap_after["handles"] - snap_before["handles"]
        self.assertLessEqual(handle_delta, 5, f"Kernel handle leak detected in list_all_meeting_windows: delta={handle_delta}")

    # ==================================================================
    # 4. ROMANIZED HINGLISH TRANSCRIPTION (PART 4.1 & 4.2)
    # ==================================================================
    def test_05_romanized_hinglish_latin_script_enforcement_and_wer(self) -> None:
        """
        Verify Hindi & Hinglish speech is rendered in Romanized Hindi (Latin script)
        mixed naturally with English, with 0 Devanagari characters and low WER.
        """
        # 1. Exact prompt benchmark utterance
        raw_whisper_deva = (
            "मैंने ये टास्क कल सबमिट कर दिया था, and then I communicated with पंकज, "
            "तो उन्होंने बोला कि wait for the customer to respond."
        )
        expected_target = (
            "Maine ye task kal submit kar diya tha, and then I communicated with Pankaj, "
            "to unhone bola ki wait for the customer to respond."
        )
        actual_out = DictationProcessor.process(
            raw_whisper_deva,
            cleanup_enabled=True,
            spoken_punctuation=False,
            output_script="roman",
        )
        self.assertIsNone(
            re.search(r"[\u0900-\u097F]", actual_out),
            f"Devanagari characters found in meeting transcript: {actual_out}",
        )
        self.assertEqual(actual_out, expected_target)
        self.assertEqual(_compute_wer(expected_target, actual_out), 0.0)

        # 2. Multi-sentence Hinglish + English code-switching benchmark suite
        corpus = [
            (
                "अच्छा, और परचेज आर्डर का स्टेटस क्या है?",
                "Achha, aur purchase order ka status kya hai?",
            ),
            (
                "राहुल ने कहा कि कोड रिव्यू आज शाम तक कंप्लीट हो जाएगा.",
                "Rahul ne kaha ki code review aaj shaam tak complete ho jayega.",
            ),
            (
                "हमने माइक्रोसॉफ्ट टीम्स पर स्क्रीन शेयर किया और बग फिक्स देखा.",
                "Humne Microsoft Teams par screen share kiya aur bug fix dekha.",
            ),
            (
                "Good morning team, आज का एजेंडा क्लाइंट मीटिंग और डेडलाइन है.",
                "Good morning team, aaj ka agenda client meeting aur deadline hai.",
            ),
        ]
        total_wer = 0.0
        for raw_in, ref_out in corpus:
            hyp_out = DictationProcessor.process(
                raw_in, cleanup_enabled=True, spoken_punctuation=False, output_script="roman"
            )
            self.assertIsNone(
                re.search(r"[\u0900-\u097F]", hyp_out),
                f"Devanagari leaked in output: {hyp_out}",
            )
            total_wer += _compute_wer(ref_out, hyp_out)

        avg_wer = total_wer / len(corpus)
        self.assertLessEqual(avg_wer, 0.05, f"Average Hinglish WER too high: {avg_wer:.3f}")

    # ==================================================================
    # 5. MULTI-SPEAKER DIARIZATION AGAINST GROUND TRUTH (PART 5 & 7.3)
    # ==================================================================
    def test_06_multi_speaker_persistent_diarization_ground_truth(self) -> None:
        """
        Evaluate PersistentSpeakerDiarizer on a controlled 3-speaker + volume + inflection + chunked
        ground-truth dataset:
        - Speaker 1: Low male voice (F0=110 Hz, formants 500/1500/2500 Hz)
        - Speaker 2: Mid voice (F0=175 Hz, formants 650/1950/2850 Hz)
        - Speaker 3: Higher female voice (F0=240 Hz, formants 820/2350/3300 Hz)
        Tests:
        1. Distinct attribution across turns (Speaker 1, Speaker 2, Speaker 3) — no 'Remote Speaker 1' collapse.
        2. Persistence across multiple progressive chunks and long pauses.
        3. Volume invariance (quiet 0.15 amplitude vs loud 0.85 amplitude).
        4. Mid-sentence pitch inflection continuity (Speaker 1 gliding 110 -> 125 Hz mid-sentence stays Speaker 1).
        """
        sr = 16000
        v1_norm = _synth_voice(110.0, [(500, 80), (1500, 120), (2500, 150)], duration_sec=2.0, sr=sr, amplitude=0.5)
        v2_norm = _synth_voice(175.0, [(650, 90), (1950, 130), (2850, 160)], duration_sec=2.0, sr=sr, amplitude=0.5)
        v3_norm = _synth_voice(240.0, [(820, 100), (2350, 140), (3300, 180)], duration_sec=2.0, sr=sr, amplitude=0.5)

        # Quiet and loud variants of Speaker 2 & Speaker 1
        v2_quiet = _synth_voice(175.0, [(650, 90), (1950, 130), (2850, 160)], duration_sec=2.0, sr=sr, amplitude=0.12)
        v1_loud = _synth_voice(110.0, [(500, 80), (1500, 120), (2500, 150)], duration_sec=2.0, sr=sr, amplitude=0.90)

        # Mid-sentence inflection of Speaker 1 (110 Hz -> 126 Hz)
        v1_inflect = _synth_voice(
            110.0, [(500, 80), (1500, 120), (2500, 150)], duration_sec=1.5, sr=sr, amplitude=0.5, f0_end_hz=126.0
        )

        # Verify pairwise cosine distances exceed clustering threshold (0.20)
        e1 = extract_speaker_embedding(v1_norm, sr)
        e2 = extract_speaker_embedding(v2_norm, sr)
        e3 = extract_speaker_embedding(v3_norm, sr)
        self.assertGreater(speaker_cosine_distance(e1, e2), 0.25)
        self.assertGreater(speaker_cosine_distance(e1, e3), 0.25)
        self.assertGreater(speaker_cosine_distance(e2, e3), 0.25)

        # Ground-truth multi-turn conversation across 2 progressive chunks
        diarizer = PersistentSpeakerDiarizer(speaker_prefix="Speaker", distance_threshold=0.20)

        ground_truth_turns = [
            # (audio_clip, start_sec, end_sec, expected_speaker, text)
            (v1_norm, 0.0, 2.0, "Speaker 1", "Good morning. I have completed the costing review."),
            (v2_norm, 2.5, 4.5, "Speaker 2", "Achha, aur purchase order ka status kya hai?"),
            (v3_norm, 5.0, 7.0, "Speaker 3", "The supplier confirmed shipment for tomorrow."),
            (v2_quiet, 7.6, 9.6, "Speaker 2", "Okay, then we can proceed with the next step."),
            # Chunk 2 after a pause: Speaker 1 speaks loudly, then inflects mid-sentence, then Speaker 3 returns
            (v1_loud, 15.0, 17.0, "Speaker 1", "Maine ye task kal submit kar diya tha."),
            (v1_inflect, 17.15, 18.65, "Speaker 1", "And then I communicated with Pankaj."),
            (v3_norm, 19.5, 21.5, "Speaker 3", "To unhone bola ki wait for the customer to respond."),
        ]

        predicted_speakers = []
        for clip, st, et, exp_spk, _ in ground_truth_turns:
            emb = extract_speaker_embedding(clip, sr)
            pred_spk, conf = diarizer.assign_speaker(
                emb, start_sec=st, end_sec=et, duration_sec=(et - st)
            )
            predicted_speakers.append(pred_spk)
            self.assertGreaterEqual(conf, 0.60)

        expected_speakers = [t[3] for t in ground_truth_turns]
        errors = sum(1 for p, e in zip(predicted_speakers, expected_speakers) if p != e)
        der = errors / float(len(expected_speakers))
        self.assertEqual(
            predicted_speakers,
            expected_speakers,
            f"Diarization mismatch! Predicted={predicted_speakers}, Expected={expected_speakers}",
        )
        self.assertEqual(der, 0.0, f"Expected 0.0% DER on ground-truth 3-speaker synthesis, got {der * 100:.1f}%")

    # ==================================================================
    # 6. STRESS, ENDURANCE, LOW-DISK GUARD & LOGGING CORRELATION (PART 6, 7.2, 8)
    # ==================================================================
    def test_07_endurance_consecutive_sessions_and_bounded_streaming_mix(self) -> None:
        """
        Run 10 consecutive recording start/stop cycles and test bounded 60s chunked WAV mixing
        to verify zero memory leaks, bounded queues, and deterministic cleanup.
        """
        ctrl = AppController(self.config)
        try:
            snap_initial = get_system_resource_snapshot()
            with patch.object(ctrl.pc_screen_audio, "start", return_value=(True, "ok")), \
                 patch.object(ctrl.pc_screen_recorder, "start", return_value=(True, "ok")), \
                 patch.object(ctrl.pc_screen_recorder, "stop", return_value=""), \
                 patch.object(ctrl.pc_screen_audio, "stop", return_value=None):
                for i in range(10):
                    ok, _ = ctrl.start_screen_recording(title=f"Endurance {i}", audio_mode="none")
                    self.assertTrue(ok)
                    ctrl.stop_screen_recording(async_finalize=False)
                    self.assertEqual(ctrl.screen_recording_state, "idle")

            snap_final = get_system_resource_snapshot()
            ram_growth_mb = snap_final["rss_mb"] - snap_initial["rss_mb"]
            self.assertLess(ram_growth_mb, 25.0, f"Excessive RAM growth across 10 sessions: {ram_growth_mb:.2f} MB")

            # Verify bounded streaming mixer on a 90-second dual-track recording (crosses 60s chunk boundary)
            rec = MeetingRecorder()
            sr = 16000
            n_samples = sr * 90  # 90 seconds
            mic_wav = self.base_path / "mic_90s.wav"
            sys_wav = self.base_path / "sys_90s.wav"
            mixed_wav = self.base_path / "mixed_90s.wav"

            t = np.linspace(0, 90.0, n_samples, endpoint=False, dtype=np.float32)
            mic_pcm = (np.sin(2.0 * np.pi * 220.0 * t) * 12000).astype(np.int16)
            sys_pcm = (np.sin(2.0 * np.pi * 440.0 * t) * 12000).astype(np.int16)

            for p, arr in ((mic_wav, mic_pcm), (sys_wav, sys_pcm)):
                with wave.open(str(p), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sr)
                    wf.writeframes(arr.tobytes())

            rec._write_mixed(mixed_wav, mic_wav, sys_wav, sr)
            self.assertTrue(mixed_wav.exists())
            slice_mid = load_wav_slice_as_16k_f32(mixed_wav, start_sec=58.0, end_sec=62.0)
            self.assertEqual(slice_mid.size, 4 * sr)
            self.assertGreater(float(np.max(np.abs(slice_mid))), 0.1)
        finally:
            ctrl.shutdown()

    def test_08_low_disk_space_guard_and_structured_correlation_logging(self) -> None:
        """Verify low-disk guard blocks recording cleanly and correlation IDs appear in structured logs."""
        ctrl = AppController(self.config)
        try:
            # Simulate impossibly high disk requirement to trigger low-disk guard
            ok, free_mb, msg = check_disk_space_mb(self.base_path, min_free_mb=999_999_999.0)
            self.assertFalse(ok)
            self.assertIn("Insufficient disk space", msg)

            with patch("voxlocal.core.controller.check_disk_space_mb", return_value=(False, 10.0, "Low disk test")):
                ok_m, msg_m = ctrl.start_meeting_recording(title="Low Disk Meeting")
                self.assertFalse(ok_m)
                self.assertEqual(ctrl.meeting_state, "idle")

                ok_s, msg_s = ctrl.start_screen_recording(title="Low Disk Screen")
                self.assertFalse(ok_s)
                self.assertEqual(ctrl.screen_recording_state, "idle")

            # Verify structured correlation IDs in logger
            cid = generate_correlation_id("test")
            with operation_context("unit_op", cid):
                controller_mod.logger.info("Structured diagnostic test message")
            recent = "\n".join(get_recent_logs(20))
            self.assertIn(f"unit_op:{cid}", recent)
        finally:
            ctrl.shutdown()


if __name__ == "__main__":
    unittest.main()

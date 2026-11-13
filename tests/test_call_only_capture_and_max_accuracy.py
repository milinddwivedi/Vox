from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
"""Comprehensive Verification Suite for:
1. IMPLEMENTATION 1 — Call-Only Meeting Recording Architecture (Core Feature 3 — Mode A)
2. IMPLEMENTATION 2 — Maximum Meeting Transcription Accuracy Pipeline (Stages A–F)
"""


import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import av
import numpy as np

from voxlocal.audio.call_detector import find_meeting_window, list_all_meeting_windows
from voxlocal.audio.meeting_recorder import _write_mixed, compute_wav_quality_metrics
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import (
    LocalSpeakerProfileStore,
    LocalWhisperTranscriber,
    PersistentSpeakerDiarizer,
    extract_subsegment_speaker_embedding,
)
from voxlocal.video.meeting_capture import (
    CaptureSourceValidator,
    GoogleMeetTabCaptureProvider,
    LocalBrowserTabCaptureBridge,
    MeetingSourceDescriptor,
    MeetingVideoRecorder,
    TeamsDesktopCaptureProvider,
    ZoomDesktopCaptureProvider,
    extract_google_meet_slug,
    resolve_meeting_source,
)


class TestCallOnlyMeetingCaptureArchitecture(unittest.TestCase):
    """Tests all mandatory Call-Only Meeting Recording (Mode A) isolation invariants."""

    def test_01_negative_desktop_fallback_strictly_blocked(self):
        """Negative Test (Section 12.2): Attempting to start MeetingVideoRecorder with
        no window, `target_hwnd=None`, or `target_hwnd=-1` (desktop sentinel) MUST fail
        closed and increment `desktop_fallback_attempts_blocked`.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            out_mp4 = os.path.join(tmpdir, "should_not_record_desktop.mp4")

            # Case 1: target_hwnd = -1 (legacy desktop sentinel)
            rec_neg1 = MeetingVideoRecorder(output_path=out_mp4, target_hwnd=-1, fps=15)
            started_1 = rec_neg1.start()
            self.assertFalse(started_1, "MeetingVideoRecorder must reject target_hwnd=-1 desktop sentinel")
            diag_1 = rec_neg1.get_capture_diagnostics()
            self.assertFalse(diag_1["desktop_capture_allowed"])
            self.assertGreaterEqual(diag_1["desktop_fallback_attempts_blocked"], 1)

            # Case 2: descriptor = None
            rec_none = MeetingVideoRecorder(output_path=out_mp4, source_descriptor=None, fps=15)
            started_2 = rec_none.start()
            self.assertFalse(started_2, "MeetingVideoRecorder must reject None source_descriptor")
            diag_2 = rec_none.get_capture_diagnostics()
            self.assertEqual(diag_2["last_reason"], "desktop_fallback_blocked")

    def test_02_teams_call_stage_prioritized_over_chat_hub(self):
        """Root-cause regression test for Meeting #34:
        When Microsoft Teams (`ms-teams.exe`) has BOTH:
          - Main Hub Window (`hwnd=66990`, `'Chat | Daily Scrum - PLM North America | Microsoft Teams'`)
          - Call Stage Window (`hwnd=2230116`, `'Daily Scrum - PLM North America | Microsoft Teams'`)
        `TeamsDesktopCaptureProvider` and `resolve_meeting_source` MUST select the Call Stage
        window (`hwnd=2230116`) and mark the `'Chat | ...'` hub window as `is_call_stage=False`.
        """
        provider = TeamsDesktopCaptureProvider()

        hub_desc = provider.inspect_window(
            hwnd=66990,
            pid=14200,
            process_name="ms-teams.exe",
            class_name="TeamsWebView",
            title="Chat | Daily Scrum - PLM North America | Microsoft Teams",
            bounds=(0, 0, 1920, 1020),
        )
        call_desc = provider.inspect_window(
            hwnd=2230116,
            pid=14200,
            process_name="ms-teams.exe",
            class_name="TeamsWebView",
            title="Daily Scrum - PLM North America | Microsoft Teams",
            bounds=(0, 0, 1920, 1020),
        )

        self.assertIsNotNone(hub_desc)
        self.assertIsNotNone(call_desc)
        self.assertFalse(hub_desc.is_call_stage, "'Chat | ...' must NOT be classified as a call stage")
        self.assertTrue(call_desc.is_call_stage, "Teams meeting window must be classified as call stage")
        self.assertGreater(call_desc.score, hub_desc.score + 150)

        # Even if caller passes the Chat Hub HWND (66990), resolve_meeting_source upgrades it to 2230116!
        with patch(
            "voxlocal.video.meeting_capture.discover_meeting_sources",
            return_value=[call_desc, hub_desc],
        ):
            resolved = resolve_meeting_source(target_hwnd=66990)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.hwnd, 2230116)
            self.assertTrue(resolved.is_call_stage)

    def test_03_teams_rebinds_or_pauses_when_navigating_to_non_meeting_view(self):
        """If the bound Teams window switches to `'Chat | ...'` or `'Files | ...'`:
        - If a dedicated Call Stage window for the same Teams PID is visible, it auto-rebinds.
        - If no Call Stage window is visible, `CaptureSourceValidator` pauses video capture
          (`reason='non_meeting_view_active'`) so private chats/files are never recorded.
        """
        provider = TeamsDesktopCaptureProvider()
        locked_desc = MeetingSourceDescriptor(
            provider_id="teams_desktop",
            platform_name="Microsoft Teams",
            hwnd=1001,
            pid=8800,
            process_name="ms-teams.exe",
            class_name="TeamsWebView",
            window_title="Daily Scrum - PLM North America | Microsoft Teams",
            meeting_id_slug="daily scrum - plm north america",
            bounds=(0, 0, 1280, 720),
            score=230,
            is_call_stage=True,
        )
        validator = CaptureSourceValidator(locked_desc)

        with patch("voxlocal.video.meeting_capture.user32.IsWindow", return_value=True), \
             patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=False), \
             patch("voxlocal.video.meeting_capture.get_window_title", return_value="Files | Troy Lee - Tax Schedule Exp.txt | Microsoft Teams"), \
             patch("voxlocal.video.meeting_capture.get_window_pid_and_process_name", return_value=(8800, "ms-teams.exe")):

            # Case A: No other call stage window exists -> must pause for privacy
            res_paused = validator.validate_frame_source(candidate_windows=[])
            self.assertFalse(res_paused.is_valid)
            self.assertEqual(res_paused.reason, "non_meeting_view_active")

            # Case B: Popped-out call stage window (hwnd=2002) exists -> must rebind automatically
            popped_out_stage = MeetingSourceDescriptor(
                provider_id="teams_desktop",
                platform_name="Microsoft Teams",
                hwnd=2002,
                pid=8800,
                process_name="ms-teams.exe",
                class_name="TeamsWebView",
                window_title="Daily Scrum - PLM North America | Microsoft Teams",
                meeting_id_slug="daily scrum - plm north america",
                bounds=(0, 0, 1280, 720),
                score=230,
                is_call_stage=True,
            )
            res_rebound = validator.validate_frame_source(candidate_windows=[popped_out_stage])
            self.assertTrue(res_rebound.is_valid)
            self.assertTrue(res_rebound.rebound)
            self.assertEqual(res_rebound.active_hwnd, 2002)
            self.assertEqual(locked_desc.hwnd, 2002)

    def test_04_google_meet_tab_isolation_blocks_unrelated_browser_tabs(self):
        """Scenario 3 (Google Meet Tab Switching):
        - When capturing Google Meet in Chrome (`Meet - abc-defg-hij`), if the user switches
          to Gmail or YouTube in the same browser window without the tab extension streaming,
          `CaptureSourceValidator` immediately blocks capture (`browser_tab_switched_away`).
        - When `LocalBrowserTabCaptureBridge` IS streaming the locked `browser_tab_id`,
          capture continues seamlessly with 100% Meet frames even while Chrome is on Gmail!
        """
        self.assertEqual(extract_google_meet_slug("Meet - qwe-rtyu-iop - Google Chrome"), "qwe-rtyu-iop")

        desc = MeetingSourceDescriptor(
            provider_id="google_meet_tab",
            platform_name="Google Meet",
            hwnd=5555,
            pid=9900,
            process_name="chrome.exe",
            class_name="Chrome_WidgetWin_1",
            window_title="Meet - qwe-rtyu-iop - Google Chrome",
            meeting_id_slug="qwe-rtyu-iop",
            browser_tab_id=42,
            bounds=(0, 0, 1280, 720),
            score=230,
            is_call_stage=True,
        )
        validator = CaptureSourceValidator(desc)
        bridge = LocalBrowserTabCaptureBridge.get_instance()
        bridge.unregister_tab(42)

        with patch("voxlocal.video.meeting_capture.user32.IsWindow", return_value=True), \
             patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=False):

            # 1. Active tab is Google Meet -> valid
            with patch("voxlocal.video.meeting_capture.get_window_title", return_value="Meet - qwe-rtyu-iop - Google Chrome"):
                res_ok = validator.validate_frame_source()
                self.assertTrue(res_ok.is_valid)

            # 2. User switches Chrome tab to Gmail (without extension streaming) -> blocked immediately!
            with patch("voxlocal.video.meeting_capture.get_window_title", return_value="Inbox (14) - user@gmail.com - Gmail - Google Chrome"):
                res_gmail = validator.validate_frame_source()
                self.assertFalse(res_gmail.is_valid)
                self.assertEqual(res_gmail.reason, "browser_tab_switched_away")

            # 3. Extension streams locked tab #42 via LocalBrowserTabCaptureBridge while user is on Gmail -> valid!
            synthetic_meet_frame = np.full((360, 640, 3), (40, 180, 60), dtype=np.uint8)
            bridge.ingest_raw_bgr_frame(
                tab_id=42,
                bgr_frame=synthetic_meet_frame,
                title="Meet - qwe-rtyu-iop",
                url="https://meet.google.com/qwe-rtyu-iop",
            )
            with patch("voxlocal.video.meeting_capture.get_window_title", return_value="Inbox (14) - user@gmail.com - Gmail - Google Chrome"):
                res_ext = validator.validate_frame_source()
                self.assertTrue(res_ext.is_valid)
                self.assertEqual(res_ext.reason, "tab_extension_stream_active")

        bridge.unregister_tab(42)

    def test_05_minimized_meeting_window_emits_privacy_frame_never_desktop(self):
        """Scenario 4 (Minimized Meeting Window):
        When the locked meeting window is minimized (`IsIconic=True`), `MeetingVideoRecorder`
        must NEVER capture the desktop behind it; it must emit the Call-Only Privacy Guard
        status frame and keep encoding valid H.264 frames at the wall-clock PTS.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            out_mp4 = os.path.join(tmpdir, "minimized_call.mp4")
            desc = MeetingSourceDescriptor(
                provider_id="zoom_desktop",
                platform_name="Zoom",
                hwnd=7777,
                pid=4321,
                process_name="zoom.exe",
                class_name="ZPContentViewWndClass",
                window_title="Zoom Meeting",
                meeting_id_slug="zoom meeting",
                bounds=(0, 0, 640, 360),
                score=225,
                is_call_stage=True,
            )

            with patch("voxlocal.video.meeting_capture.user32.IsWindow", return_value=True), \
                 patch("voxlocal.video.meeting_capture.user32.IsIconic", return_value=True), \
                 patch("voxlocal.video.meeting_capture.get_window_title", return_value="Zoom Meeting"), \
                 patch("voxlocal.video.meeting_capture.get_window_class_name", return_value="ZPContentViewWndClass"), \
                 patch("voxlocal.video.meeting_capture.get_window_rect", return_value=(0, 0, 640, 360)), \
                 patch("voxlocal.video.meeting_capture.discover_meeting_sources", return_value=[desc]):

                rec = MeetingVideoRecorder(
                    output_path=out_mp4,
                    source_descriptor=desc,
                    fps=10,
                    max_height=360,
                )
                self.assertTrue(rec.start())
                time.sleep(0.45)
                rec.stop()

                diag = rec.get_capture_diagnostics()
                self.assertGreater(diag["frames_encoded"], 0)
                self.assertGreater(diag["frames_paused_privacy"], 0)
                self.assertIn("window_minimized", diag["pause_reasons"])
                self.assertTrue(os.path.exists(out_mp4))
                self.assertGreater(os.path.getsize(out_mp4), 500)


class TestMaxTranscriptionAccuracyPipeline(unittest.TestCase):
    """Tests Stages A–F of IMPLEMENTATION 2 (Maximum Meeting Transcription Accuracy)."""

    def test_06_stage_a_mixed_audio_preserves_remote_speaker_level_and_raw_backup(self):
        """Stage A: Verify `_write_mixed` preserves `system_track_raw.wav` and keeps remote
        speaker gain at ~0.94 when local mic is silent (fixing the 0.55 attenuation bug).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            mic_wav = tmp / "mic_track.wav"
            sys_wav = tmp / "system_track.wav"
            mix_wav = tmp / "meeting_mixed.wav"

            sr = 16000
            t = np.linspace(0, 1.0, sr, endpoint=False)
            # Silent mic, active remote speaker at 0.50 amplitude
            mic_pcm = np.zeros(sr, dtype=np.int16)
            sys_pcm = (np.sin(2 * np.pi * 440 * t) * 0.50 * 32767).astype(np.int16)

            for path, pcm in ((mic_wav, mic_pcm), (sys_wav, sys_pcm)):
                with wave.open(str(path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sr)
                    wf.writeframes(pcm.tobytes())

            dur = _write_mixed(mic_wav, sys_wav, mix_wav)
            self.assertAlmostEqual(dur, 1.0, places=2)
            self.assertTrue((tmp / "system_track_raw.wav").exists(), "system_track_raw.wav must be preserved")

            metrics = compute_wav_quality_metrics(mix_wav)
            self.assertTrue(metrics["exists"])
            # With gentle boost + 0.94 scale, peak should be >= 0.65 (previously it was crushed to ~0.27)
            self.assertGreaterEqual(metrics["peak"], 0.60)
            self.assertEqual(metrics["clipping_ratio"], 0.0)

    def test_07_stage_d_indian_english_hinglish_and_plm_domain_intelligence(self):
        """Stage D: Verify Indian-English phonetic rules, Devanagari technical loanword
        transliteration, and PLM / WFX / Scrum proper-noun normalization.
        """
        dp = DictationProcessor()

        # 1. Phonetic / domain rules for WFX, PLM, HubSpot, subcategory, barcode, names
        raw_1 = "in double u f x p l m we updated the sub category and bar code in hub spot for american tall and troy lee"
        cleaned_1 = dp.process(raw_1)
        self.assertIn("WFX", cleaned_1)
        self.assertIn("PLM", cleaned_1)
        self.assertIn("subcategory", cleaned_1.lower())
        self.assertIn("barcode", cleaned_1.lower())
        self.assertIn("HubSpot", cleaned_1)
        self.assertIn("American Tall", cleaned_1)
        self.assertIn("Troy Lee", cleaned_1)

        # 2. Proper names from real meetings
        raw_2 = "hemant kumar and rum jum discussed the dailyes kram call with astha dhiman"
        cleaned_2 = dp.process(raw_2)
        self.assertIn("Hemanth Kumar", cleaned_2)
        self.assertIn("Rumjhum", cleaned_2)
        self.assertIn("Daily Scrum", cleaned_2)

        # 3. Devanagari technical loanwords -> English script
        raw_3 = "स्क्रम कॉल में डब्ल्यूएफएक्स पीएलएम का सबकैटेगरी और बारकोड एक्सेल में एक्सपोर्ट किया"
        roman_3 = dp.ensure_english_script(raw_3)
        self.assertIn("scrum", roman_3.lower())
        self.assertIn("wfx", roman_3.lower())
        self.assertIn("plm", roman_3.lower())
        self.assertIn("subcategory", roman_3.lower())
        self.assertIn("barcode", roman_3.lower())
        self.assertIn("excel", roman_3.lower())

    def test_08_stage_e_subsegment_diarization_overlap_and_speaker_profiles(self):
        """Stage E: Verify sub-segment sliding-window speaker embeddings, crosstalk/overlap
        detection, and persistent `LocalSpeakerProfileStore`.
        """
        sr = 16000
        t = np.linspace(0, 2.4, int(sr * 2.4), endpoint=False)

        # Speaker A: low fundamental (~125 Hz + harmonics)
        voice_a = (
            0.5 * np.sin(2 * np.pi * 125 * t)
            + 0.3 * np.sin(2 * np.pi * 250 * t)
            + 0.15 * np.sin(2 * np.pi * 375 * t)
        ).astype(np.float32)

        # Speaker B: higher fundamental (~235 Hz + harmonics)
        voice_b = (
            0.5 * np.sin(2 * np.pi * 235 * t)
            + 0.3 * np.sin(2 * np.pi * 470 * t)
            + 0.15 * np.sin(2 * np.pi * 705 * t)
        ).astype(np.float32)

        emb_a, disp_a = extract_subsegment_speaker_embedding(voice_a, sr)
        emb_b, disp_b = extract_subsegment_speaker_embedding(voice_b, sr)
        self.assertIsNotNone(emb_a)
        self.assertIsNotNone(emb_b)
        self.assertLess(disp_a, 0.10, "Single consistent speaker must have low internal dispersion")

        # Crosstalk segment: first half Speaker A, second half Speaker B
        mixed_ab = np.concatenate([voice_a[: sr], voice_b[: sr]])
        emb_mix, disp_mix = extract_subsegment_speaker_embedding(mixed_ab, sr)
        self.assertIsNotNone(emb_mix)
        self.assertGreater(disp_mix, disp_a, "Speaker change inside segment must increase dispersion")

        # Persistent Speaker Profile Store enrollment & matching
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "speaker_profiles.json"
            store = LocalSpeakerProfileStore(path=store_path)
            store.enroll_speaker("Hemanth Kumar", emb_a)
            store.enroll_speaker("Astha Dhiman", emb_b)

            diarizer = PersistentSpeakerDiarizer(
                similarity_threshold=0.78,
                profile_store=store,
            )
            res_a = diarizer.assign_speaker(voice_a, sr)
            res_b = diarizer.assign_speaker(voice_b, sr)
            label_a = res_a[0] if isinstance(res_a, tuple) else res_a
            label_b = res_b[0] if isinstance(res_b, tuple) else res_b
            self.assertEqual(label_a, "Hemanth Kumar")
            self.assertEqual(label_b, "Astha Dhiman")

    def test_09_stage_f_word_timestamps_and_reconcile_output_script_signature(self):
        """Stage F: Verify `reconcile_meeting_transcript` accepts `output_script='roman'`
        and preserves word-level timestamps (`words`, `low_confidence`, `overlap`).
        """
        transcriber = LocalWhisperTranscriber(model_name="small")
        segs = [
            {
                "start": 0.0,
                "end": 2.1,
                "speaker": "Speaker 1 (Remote)",
                "text": "in double u f x p l m we fixed the sub category",
                "confidence": 0.91,
                "words": [
                    {"word": "in", "start": 0.05, "end": 0.20, "probability": 0.95},
                    {"word": "WFX", "start": 0.21, "end": 0.65, "probability": 0.92},
                ],
                "low_confidence": False,
                "overlap": False,
            }
        ]
        reconciled = transcriber.reconcile_meeting_transcript(
            segs,
            output_script="roman",
        )
        self.assertEqual(len(reconciled), 1)
        self.assertIn("WFX", reconciled[0]["text"])
        self.assertIn("PLM", reconciled[0]["text"])
        self.assertIn("subcategory", reconciled[0]["text"].lower())


if __name__ == "__main__":
    unittest.main()

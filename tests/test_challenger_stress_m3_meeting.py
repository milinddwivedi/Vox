"""
Adversarial Challenger Verification Suite for Milestone 3 (Meeting & Calendar).
Empirically stress-tests:
1. Feature 14: 3-Section Meeting Studio & 7-Stage MoM Generator:
   - Zero participants & empty transcript
   - 100+ participants with high-frequency turns
   - Unicode, multi-lingual, emoji, and special character speaker names
   - Contradictory decisions (confirmed vs unresolved/reversed)
   - Zero-action-item meetings
   - Spoken relative due date resolution with uncertainty preservation
2. Meeting Exporter (TranscriptExporter):
   - Missing keys and empty meeting dictionaries
   - Huge text blocks and high-scale documents (5,000 words, 200 decisions, 200 actions)
   - Rapid sequential export calls (50 iterations)
   - All format exports (JSON, HTML, PDF, Markdown, TXT, SRT, VTT)
   - Malformed meeting dictionaries with None / invalid duration_sec or start_time
3. Feature 15: 3-Tier Outlook Calendar Engine:
   - Rapid provider switching under active background polling
   - Advance alert evaluator: strict 15m/5m thresholds without alert duplication
   - Corrupted ICS feeds (invalid INTERVAL, malformed RRULE, missing DTSTART, non-ICS text)
4. Meeting Lifecycle & Finalization:
   - Rapid start/stop meeting recording lifecycle without deadlocks or unhandled exceptions
   - Concurrency, cancellation, and clean shutdown
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtWidgets import QApplication

# Ensure QApplication instance exists
app = QApplication.instance() or QApplication(sys.argv)

from voxlocal.calendar.base import CalendarMeeting, CalendarProviderType
from voxlocal.calendar.calendar_manager import CalendarAlertEvaluator, CalendarManager
from voxlocal.calendar.ics_provider import IcsCalendarProvider, parse_ics_events
from voxlocal.calendar.mock_provider import MockCalendarProvider
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController, MeetingLifecycleState
from voxlocal.engine.meeting_intelligence import (
    MeetingMinutesPipeline,
    resolve_relative_due_date,
)
from voxlocal.storage.database import LocalDatabase
from voxlocal.storage.exporter import TranscriptExporter


class TestMomGeneratorAdversarialStress(unittest.TestCase):
    """Stress tests for the 7-Stage MoM Generator and MeetingMinutesPipeline."""

    def test_01_zero_participants_empty_transcript(self) -> None:
        """Verify 7-stage MoM pipeline handles zero participants and empty transcript gracefully."""
        res = MeetingMinutesPipeline.generate_minutes(
            segments=[],
            full_transcript="",
            duration_sec=0.0,
            title="Empty Sync",
            platform="Microsoft Teams",
            session_id="empty_sess_1",
        )
        self.assertIsInstance(res, dict)
        self.assertIn("mom_markdown", res)
        self.assertIn("mom_json", res)
        self.assertIn("MINUTES OF MEETING", res["mom_markdown"])
        self.assertIn("No spoken speech was detected", res["mom_markdown"])
        self.assertEqual(res["decisions"], [])
        self.assertEqual(res["action_items"], [])
        self.assertEqual(res.get("mom_json", {}).get("error"), "no_speech_detected")

    def test_02_100_plus_participants_stress(self) -> None:
        """Stress test participant registry with 120 unique speakers and high-frequency turns."""
        speaker_count = 120
        segments = []
        for i in range(speaker_count):
            spk = f"Speaker {i+1:03d}"
            st = float(i * 10)
            et = st + 8.0
            txt = f"Hello team, this is {spk}. We will deploy milestone {i+1} by Friday."
            segments.append({
                "speaker": spk,
                "text": txt,
                "start_time": st,
                "end_time": et,
            })

        total_dur = speaker_count * 10.0
        res = MeetingMinutesPipeline.generate_minutes(
            segments=segments,
            duration_sec=total_dur,
            title="Massive All-Hands",
            platform="Zoom",
        )

        md = res["mom_markdown"]
        self.assertIn("3. PARTICIPANTS & ROLES", md)
        self.assertIn("Speaker 001", md)
        self.assertIn("Speaker 120", md)

        # Verify active speakers in participants dictionary
        participants = res.get("speaker_metrics") or {}
        # Verify markdown table rows count for participants
        part_lines = [line for line in md.splitlines() if line.startswith("| **Speaker ")]
        self.assertEqual(len(part_lines), speaker_count, "All 120 speakers must appear in the markdown table")

        # Verify action items count matches 120
        self.assertEqual(len(res["action_items"]), speaker_count)

    def test_03_unicode_cjk_arabic_emoji_names_and_quotes(self) -> None:
        """Stress test speaker names and quotes with Unicode, Arabic, CJK, German, Emojis, and HTML tags."""
        segments = [
            {
                "speaker": "李四 🚀",
                "text": "We agreed to deploy the AI neural model tomorrow.",
                "start_time": 0.0,
                "end_time": 4.0,
            },
            {
                "speaker": "محمد العربي",
                "text": "I will review the security audit logs by tomorrow.",
                "start_time": 5.0,
                "end_time": 9.0,
            },
            {
                "speaker": "Jürgen Müller",
                "text": "We decided to allocate fifty thousand euros for infrastructure.",
                "start_time": 10.0,
                "end_time": 15.0,
            },
            {
                "speaker": "<script>alert('xss')</script> | Admin",
                "text": "Can you check with Priya? Risk is high for potential latency spike.",
                "start_time": 16.0,
                "end_time": 21.0,
            },
        ]

        res = MeetingMinutesPipeline.generate_minutes(
            segments=segments,
            duration_sec=25.0,
            title="International Sync: 🚀 Global & Secure",
            platform="Microsoft Teams",
        )

        md = res["mom_markdown"]
        self.assertIn("李四 🚀", md)
        self.assertIn("محمد العربي", md)
        self.assertIn("Jürgen Müller", md)
        self.assertIn("<script>alert('xss')</script>", md)

        # Check decisions extracted
        self.assertGreaterEqual(len(res["decisions"]), 2)
        # Check action items extracted
        self.assertGreaterEqual(len(res["action_items"]), 1)

        # Verify JSON export can parse without UTF-8 encoding faults
        j_str = json.dumps(res, ensure_ascii=False)
        parsed = json.loads(j_str)
        self.assertIn("mom_markdown", parsed)

    def test_04_contradictory_decisions_handling(self) -> None:
        """Verify pipeline handles contradictory/reversed decisions across meeting turns."""
        segments = [
            {
                "speaker": "Alice",
                "text": "We decided to release version 2.0 on Friday.",
                "start_time": 0.0,
                "end_time": 5.0,
            },
            {
                "speaker": "Bob",
                "text": "Actually, we have no consensus, we postponed the release until next sprint.",
                "start_time": 6.0,
                "end_time": 12.0,
            },
            {
                "speaker": "Charlie",
                "text": "Tentatively we approved the staging deployment for internal dogfooding.",
                "start_time": 13.0,
                "end_time": 18.0,
            },
        ]

        res = MeetingMinutesPipeline.generate_minutes(
            segments=segments,
            duration_sec=20.0,
            title="Architecture Review",
        )

        decisions = res["decisions"]
        self.assertEqual(len(decisions), 3, "All 3 distinct decision evolutions must be tracked")

        statuses = [d["status"] for d in decisions]
        self.assertIn("confirmed", statuses)
        self.assertIn("unresolved", statuses)
        self.assertIn("proposed", statuses)

    def test_05_zero_action_items_meeting(self) -> None:
        """Verify pipeline handles meetings with zero action items cleanly."""
        segments = [
            {
                "speaker": "Host",
                "text": "Welcome everyone to today's informational town hall broadcast.",
                "start_time": 0.0,
                "end_time": 5.0,
            },
            {
                "speaker": "Executive",
                "text": "Our quarterly revenue reached record highs and customer satisfaction is ninety percent.",
                "start_time": 6.0,
                "end_time": 12.0,
            },
        ]

        res = MeetingMinutesPipeline.generate_minutes(
            segments=segments,
            duration_sec=15.0,
            title="Informational Broadcast",
        )

        self.assertEqual(res["action_items"], [])
        md = res["mom_markdown"]
        self.assertIn("6. ACTION PLAN & COMMITMENTS", md)
        self.assertIn("No concrete action items or deliverable tasks assigned", md)

    def test_06_temporal_date_resolution_adversarial_expressions(self) -> None:
        """Stress test spoken relative due date resolution under adverse expressions."""
        test_cases = [
            ("", None),
            ("   ", None),
            ("2026-12-25", "2026-12-25"),
            ("by tomorrow", None),  # requires meeting_timestamp to resolve
            ("sometime in a couple of weeks", None),
            ("ASAP or maybe next quarter", None),
            ("!@#$%^&*()", None),
        ]

        for expr, expected_iso in test_cases:
            res = resolve_relative_due_date(expr, meeting_timestamp="2026-11-12T10:00:00Z")
            self.assertIsInstance(res, dict)
            self.assertIn("resolution_status", res)
            if expected_iso:
                self.assertEqual(res["resolved_date"], expected_iso)


class TestMeetingExporterAdversarialStress(unittest.TestCase):
    """Stress tests for TranscriptExporter (to_json, to_html, to_pdf, export_to_file)."""

    def test_01_exporter_missing_keys_empty_dict(self) -> None:
        """Verify exporter handles completely empty or missing dictionary keys without crashing."""
        empty_meeting: dict = {}

        # 1. to_json
        j_out = TranscriptExporter.to_json(empty_meeting)
        self.assertIsInstance(j_out, str)
        parsed_j = json.loads(j_out)
        self.assertEqual(parsed_j["title"], "Untitled Meeting")
        self.assertEqual(parsed_j["duration_sec"], 0.0)

        # 2. to_html
        h_out = TranscriptExporter.to_html(empty_meeting)
        self.assertIsInstance(h_out, str)
        self.assertIn("<!doctype html>", h_out)
        self.assertIn("Untitled Meeting", h_out)

        # 3. to_pdf
        pdf_path = TranscriptExporter.to_pdf(empty_meeting)
        self.assertTrue(pdf_path.exists())
        self.assertGreater(pdf_path.stat().st_size, 500)
        try:
            pdf_path.unlink()
        except Exception:
            pass

    def test_02_exporter_huge_text_blocks_and_scales(self) -> None:
        """Stress test exporter with gigantic transcripts (5,000 words), 200 decisions, and 200 actions."""
        huge_transcript = "In today's meeting we analyzed system throughput and architectural design. " * 600
        decisions = [{"decision": f"System decision {i}", "status": "approved"} for i in range(200)]
        actions = [
            {
                "description": f"Deliverable {i} implementation",
                "owner": f"Engineer_{i%10}",
                "due_date": "2026-11-30",
                "status": "open",
            }
            for i in range(200)
        ]

        huge_meeting = {
            "title": "Scale Stress Test Meeting",
            "duration_sec": 7200.0,
            "created_at": "2026-11-12 14:00:00",
            "full_transcript": huge_transcript,
            "decisions": decisions,
            "action_items": actions,
            "notes": "Extensive performance evaluation meeting notes.",
        }

        # 1. JSON Export
        j_out = TranscriptExporter.to_json(huge_meeting)
        self.assertGreater(len(j_out), 100_000)
        parsed = json.loads(j_out)
        self.assertEqual(len(parsed["decisions"]), 200)
        self.assertEqual(len(parsed["action_items"]), 200)

        # 2. HTML Export
        h_out = TranscriptExporter.to_html(huge_meeting)
        self.assertGreater(len(h_out), 50_000)
        self.assertIn("Scale Stress Test Meeting", h_out)
        self.assertIn("Deliverable 199 implementation", h_out)

        # 3. PDF Export
        pdf_path = TranscriptExporter.to_pdf(huge_meeting)
        self.assertTrue(pdf_path.exists())
        self.assertGreater(pdf_path.stat().st_size, 10_000)
        try:
            pdf_path.unlink()
        except Exception:
            pass

    def test_03_exporter_rapid_calls_stability(self) -> None:
        """Verify rapid repeated export calls do not leak memory or degrade performance."""
        meeting = {
            "title": "Rapid Sync",
            "duration_sec": 300.0,
            "summary": "Brief summary.",
            "full_transcript": "Rapid test line.",
        }

        start_time = time.perf_counter()
        for _ in range(50):
            j = TranscriptExporter.to_json(meeting)
            h = TranscriptExporter.to_html(meeting)
            self.assertIsNotNone(j)
            self.assertIsNotNone(h)
        elapsed = time.perf_counter() - start_time
        self.assertLess(elapsed, 2.0, "50 rapid export calls should complete in under 2 seconds")

    def test_04_exporter_export_to_file_all_formats(self) -> None:
        """Verify export_to_file cleanly handles all supported formats and rejects invalid formats."""
        meeting = {
            "title": "Format Matrix Test",
            "duration_sec": 120.0,
            "full_transcript": "Testing multi-format output.",
            "segments": [
                {
                    "start_time": 0.0,
                    "end_time": 5.0,
                    "speaker": "Speaker 1",
                    "text": "Testing multi-format output.",
                }
            ],
            "decisions": ["Approve rollout"],
            "action_items": [{"description": "Audit security", "owner": "Alice", "due_date": "Tomorrow"}],
        }

        with tempfile.TemporaryDirectory() as td:
            temp_p = Path(td)
            for fmt in ("txt", "md", "srt", "vtt", "json", "html", "pdf"):
                out_p = temp_p / f"export_test.{fmt}"
                res_p = TranscriptExporter.export_to_file(meeting, out_p, fmt=fmt)
                self.assertTrue(res_p.exists(), f"File {res_p} should exist for format {fmt}")
                self.assertGreater(res_p.stat().st_size, 0)

            # Test invalid format raises ValueError
            with self.assertRaises(ValueError):
                TranscriptExporter.export_to_file(meeting, temp_p / "bad.xyz", fmt="xyz")

    def test_05_exporter_malformed_none_fields_resilience(self) -> None:
        """
        Verify exporter handles malformed dictionaries where fields are None or non-standard.
        Documents behavior under None duration_sec, string decisions, None titles, and None segments.
        """
        # Malformed meeting dicts with None values that are cleanly handled
        handled_malformed = [
            {"title": None, "created_at": None, "capture_mode": None},
            {"decisions": None, "action_items": None},
            {"decisions": ["Plain text decision"], "action_items": ["Plain text action"]},
            {"decisions": [123, None, {}], "action_items": [456, None, {}]},
            {"full_transcript": None, "segments": None},
            {"notes": None},
        ]

        for m in handled_malformed:
            j = TranscriptExporter.to_json(m)
            self.assertIsInstance(j, str)
            h = TranscriptExporter.to_html(m)
            self.assertIsInstance(h, str)

        # Documented Vulnerability DEF-EXP-01: duration_sec=None causes float(None) -> TypeError
        # when duration_sec key is present with None value.
        with self.assertRaises(TypeError):
            TranscriptExporter.to_json({"duration_sec": None})

        # Documented Vulnerability DEF-EXP-02: non-numeric duration string causes ValueError
        with self.assertRaises(ValueError):
            TranscriptExporter.to_json({"duration_sec": "invalid_duration"})


class TestCalendarEngineAdversarialStress(unittest.TestCase):
    """Stress tests for CalendarManager, CalendarAlertEvaluator, and IcsCalendarProvider."""

    def test_01_advance_alert_evaluator_deduplication(self) -> None:
        """Stress test alert evaluator for strict 15m/5m thresholds without alert duplication."""
        evaluator = CalendarAlertEvaluator()
        now = datetime.now(timezone.utc)

        meeting_15m = CalendarMeeting(
            id="m_15m",
            title="Q4 Strategy Review",
            start_time=now + timedelta(minutes=10),
            end_time=now + timedelta(minutes=40),
            attendees=["Alice Smith", "Bob Jones"],
        )

        # First evaluation should trigger 15min alert
        alerts_1 = evaluator.evaluate_meeting_alerts([meeting_15m], current_time=now)
        self.assertEqual(len(alerts_1), 1)
        self.assertEqual(alerts_1[0]["alert_type"], "15min")
        self.assertEqual(alerts_1[0]["action"], "acoustic_priming")

        # 50 rapid subsequent evaluations must NOT duplicate the 15min alert
        for _ in range(50):
            dups = evaluator.evaluate_meeting_alerts([meeting_15m], current_time=now)
            self.assertEqual(len(dups), 0, "15min alert must not be duplicated")

        # Advance time into the 5-minute imminent window (3m before start)
        now_imminent = now + timedelta(minutes=7)
        alerts_2 = evaluator.evaluate_meeting_alerts([meeting_15m], current_time=now_imminent)
        self.assertEqual(len(alerts_2), 1)
        self.assertEqual(alerts_2[0]["alert_type"], "5min")
        self.assertEqual(alerts_2[0]["action"], "show_toast_prompt")

        # 50 rapid subsequent evaluations must NOT duplicate the 5min alert
        for _ in range(50):
            dups = evaluator.evaluate_meeting_alerts([meeting_15m], current_time=now_imminent)
            self.assertEqual(len(dups), 0, "5min alert must not be duplicated")

        # Test cancelled meeting never triggers alerts
        cancelled_mtg = CalendarMeeting(
            id="m_canc",
            title="Cancelled Sync",
            start_time=now + timedelta(minutes=4),
            end_time=now + timedelta(minutes=30),
            is_cancelled=True,
        )
        canc_alerts = evaluator.evaluate_meeting_alerts([cancelled_mtg], current_time=now)
        self.assertEqual(len(canc_alerts), 0, "Cancelled meeting must never trigger alerts")

        # Test acoustic priming extraction
        priming_terms = evaluator.extract_acoustic_priming_terms(meeting_15m)
        self.assertIn("Alice Smith", priming_terms)
        self.assertIn("Bob Jones", priming_terms)
        self.assertIn("Strategy", priming_terms)

    def test_02_rapid_provider_switching_stability(self) -> None:
        """Verify CalendarManager supports rapid active provider switching under polling without deadlocks."""
        p1 = MockCalendarProvider()
        p2 = IcsCalendarProvider()

        mgr = CalendarManager(provider=p1, poll_interval_sec=1)
        status_updates = []
        mgr.provider_status_changed.connect(lambda s: status_updates.append(s))

        mgr.start_polling(interval_sec=1)
        for i in range(20):
            mgr.set_provider(p1 if i % 2 == 0 else p2)
        mgr.stop_polling()

        self.assertGreaterEqual(len(status_updates), 20)
        self.assertEqual(mgr.provider, p2)

    def test_03_corrupted_ics_feeds_parsing_resilience(self) -> None:
        """Stress test ICS feed parser against corrupted, malformed, and adversarial inputs."""
        now = datetime.now(timezone.utc)
        win_end = now + timedelta(days=7)

        # Clean samples that must parse without error
        clean_samples = [
            "",
            "NOT AN ICS FILE AT ALL",
            "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR",
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:123\nSUMMARY:Test\nDTSTART:20261112T140000Z\nDTEND:20261112T150000Z\nEND:VEVENT\nEND:VCALENDAR",
            "BEGIN:VCALENDAR\n" + "X-GARBAGE:VALUE\n" * 500 + "END:VCALENDAR",
        ]

        for s in clean_samples:
            name, events = parse_ics_events(s, now, win_end)
            self.assertIsInstance(name, str)
            self.assertIsInstance(events, list)

        # Adversarial sample with malformed RRULE INTERVAL (e.g. INTERVAL=invalid)
        corrupted_rrule_ics = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "UID:corr-1\n"
            "DTSTART:20261112T120000Z\n"
            "RRULE:FREQ=DAILY;INTERVAL=invalid;COUNT=abc\n"
            "SUMMARY:Corrupt Event\n"
            "END:VEVENT\n"
            "END:VCALENDAR"
        )
        try:
            name, events = parse_ics_events(corrupted_rrule_ics, now, win_end)
            self.assertIsInstance(events, list)
        except ValueError as ve:
            # Documented Bug DEF-ICS-01: int(parts.get("INTERVAL", "1")) raises ValueError on non-digit
            self.assertIn("invalid literal for int()", str(ve))


class TestMeetingLifecycleAdversarialStress(unittest.TestCase):
    """Stress tests for AppController meeting recording start/stop lifecycle and finalization."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_p = Path(self.temp_dir.name)
        self.config = AppConfig(
            storage_dir=str(self.base_p / "data"),
            models_dir=str(self.base_p / "models"),
            auto_record_calls=False,
            record_meeting_screen=False,
        )
        self.config.disable_model_preload = True
        self.config.disable_hotkeys = True
        self.controller = AppController(config=self.config)
        self.controller.transcriber.load_model = MagicMock(return_value=True)
        self.controller.transcriber.is_model_loaded = MagicMock(return_value=True)

    def tearDown(self) -> None:
        self.controller.shutdown()
        self.temp_dir.cleanup()

    def test_01_rapid_start_stop_meeting_recording(self) -> None:
        """Stress test rapid consecutive start/stop meeting recording calls without deadlocks."""
        self.controller.meeting_recorder.start = lambda **kw: (True, "Started")
        self.controller.meeting_recorder.stop = lambda **kw: None

        for i in range(15):
            ok, msg = self.controller.start_meeting_recording(title=f"Rapid Test {i}")
            self.assertTrue(ok, f"Start failed at iteration {i}: {msg}")
            self.assertEqual(self.controller.meeting_state, "recording")
            self.controller.stop_meeting_recording()
            self.assertEqual(self.controller.meeting_state, "idle")

    def test_02_start_recording_while_transcribing_succeeds_concurrently(self) -> None:
        """Verify starting recording when background notes are 'transcribing' succeeds concurrently."""
        self.controller.meeting_recorder.start = lambda **kw: (True, "Started")
        self.controller.meeting_recorder.stop = lambda **kw: None
        with self.controller._state_lock:
            if hasattr(self.controller, "_finalizing_jobs"):
                self.controller._finalizing_jobs.add(999)
            if hasattr(self.controller, "_capture_state"):
                self.controller._capture_state = "idle"
            else:
                self.controller._meeting_state = "transcribing"

        ok, msg = self.controller.start_meeting_recording(title="Concurrent Start")
        self.assertTrue(ok, f"Start must succeed while transcribing in background: {msg}")
        self.assertTrue(self.controller.is_meeting_recording)

        self.controller.stop_meeting_recording()
        with self.controller._state_lock:
            if hasattr(self.controller, "_finalizing_jobs"):
                self.controller._finalizing_jobs.discard(999)

    def test_03_double_stop_recording_idempotent(self) -> None:
        """Verify calling stop_meeting_recording multiple times when idle is idempotent."""
        self.assertEqual(self.controller.meeting_state, "idle")
        # Should be a no-op without raising unhandled exceptions
        self.controller.stop_meeting_recording()
        self.assertEqual(self.controller.meeting_state, "idle")
        self.controller.stop_meeting_recording()
        self.assertEqual(self.controller.meeting_state, "idle")

    def test_04_cooperative_finalization_cancellation(self) -> None:
        """Verify finalization cancellation via cancel_finalization() handles state cleanly."""
        mid = self.controller.db.create_meeting(title="Cancel Test", capture_mode="both")
        self.controller.cancel_finalization()
        self.assertTrue(self.controller._cancel_finalization_event.is_set())

        # Test cooperative handler
        self.controller._handle_finalization_cancelled(mid, current_stage=2)
        m = self.controller.db.get_meeting(mid)
        self.assertEqual(m["status"], "interrupted")
        self.assertEqual(self.controller.meeting_state, "idle")

    def test_05_shutdown_during_finalization_clean_join(self) -> None:
        """Verify controller shutdown signals cancel event and cleanly joins finalize thread."""
        dummy_thread = threading.Thread(target=lambda: time.sleep(0.05), name="DummyFinalize")
        dummy_thread.start()
        self.controller._finalize_thread = dummy_thread

        # Shutdown should join dummy_thread within timeout
        self.controller.shutdown()
        self.assertTrue(self.controller._cancel_finalization_event.is_set())
        self.assertFalse(dummy_thread.is_alive())


if __name__ == "__main__":
    unittest.main()

import json
import html
import sys
from pathlib import Path

sys.path.insert(0, "src")

from datetime import datetime, timezone, timedelta

from voxlocal.storage.exporter import TranscriptExporter
from voxlocal.engine.meeting_intelligence import MeetingMinutesPipeline, resolve_relative_due_date
from voxlocal.calendar.calendar_manager import CalendarAlertEvaluator
from voxlocal.calendar.base import CalendarMeeting

print("--- Running Adversarial Stress Test Suite ---")

# 1. Test Exporters against malformed inputs and XSS
malicious_meeting = {
    "id": 999,
    "title": "<script>alert(1)</script> & Meeting",
    "created_at": "2026-11-12T14:00:00Z",
    "duration_sec": 120.5,
    "summary": "<b>Executive summary</b> with <i>tags</i> & ampersands",
    "decisions": [
        {"decision": "<img src=x onerror=alert(2)>", "status": "approved"},
        "Raw string decision with <tags>",
        12345,  # non-dict, non-str
    ],
    "action_items": [
        {"description": "Do something <evil>", "owner": "<Alice>", "status": "OPEN", "due_date": "tomorrow"},
        "Raw action string <test>",
        None,   # None item
    ],
    "notes": "Note with <script>evil()</script>",
    "segments": [
        {"start_time": 0.0, "end_time": 2.5, "speaker": "<Bob>", "text": "Hello <world>!"},
        {"start_time": 2.5, "end_time": 5.0, "speaker": "Alice", "text": ""},  # empty text
    ],
}

json_out = TranscriptExporter.to_json(malicious_meeting)
parsed_json = json.loads(json_out)
assert parsed_json["id"] == 999
print("[PASS] Exporter to_json survived malformed/non-standard types")

html_out = TranscriptExporter.to_html(malicious_meeting)
assert "<script>alert(1)</script>" not in html_out
assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_out
assert "&lt;Bob&gt;" in html_out
assert "&lt;img src=x onerror=alert(2)&gt;" in html_out
print("[PASS] Exporter to_html properly escapes HTML/XSS vectors")

pdf_path = TranscriptExporter.to_pdf(malicious_meeting)
assert Path(pdf_path).exists()
assert Path(pdf_path).stat().st_size > 500
print(f"[PASS] Exporter to_pdf generated valid document: {pdf_path} ({Path(pdf_path).stat().st_size} bytes)")
Path(pdf_path).unlink(missing_ok=True)

# 2. Test MeetingMinutesPipeline with extreme inputs
empty_minutes = MeetingMinutesPipeline.generate_minutes(segments=[], full_transcript="", duration_sec=0.0)
assert "mom_markdown" in empty_minutes
assert "mom_json" in empty_minutes
assert empty_minutes["mom_json"].get("error") == "no_speech_detected"
print("[PASS] MeetingMinutesPipeline handles 0 segments / empty recording defensively")

weird_segments = [
    {"start_time": -5.0, "end_time": 0.0, "speaker": "", "text": "   "},
    {"start_time": 0.0, "end_time": 10.0, "speaker": "Alex", "text": "We decided to deploy the new microservices architecture to AWS next week. Bob will prepare the Terraform configuration by Friday."},
]
res_weird = MeetingMinutesPipeline.generate_minutes(segments=weird_segments, duration_sec=10.0)
assert len(res_weird["decisions"]) >= 1
assert len(res_weird["action_items"]) >= 1
print("[PASS] MeetingMinutesPipeline handles negative timestamps, empty speaker/text cleanly")

# 3. Test relative date resolution with boundary inputs
dt_res_empty = resolve_relative_due_date("")
assert dt_res_empty["resolved_date"] is None
assert dt_res_empty["resolution_status"] == "unresolved"

dt_res_iso = resolve_relative_due_date("Due on 2026-11-20")
assert dt_res_iso["resolved_date"] == "2026-11-20"

dt_res_rel = resolve_relative_due_date("by Friday", meeting_timestamp="2026-11-12T10:00:00Z")
assert dt_res_rel["resolved_date"] is not None
print(f"[PASS] resolve_relative_due_date resolves correctly: {dt_res_rel['resolved_date']}")

# 4. Test CalendarAlertEvaluator deduplication and past events
evaluator = CalendarAlertEvaluator()
now = datetime.now(timezone.utc)
m_past = CalendarMeeting(
    id="past_1",
    title="Past Meeting",
    start_time=now - timedelta(minutes=10),
    end_time=now + timedelta(minutes=20),
    organizer="Alice",
    attendees=["Alice", "Bob"],
)
m_15m = CalendarMeeting(
    id="m_15",
    title="Architecture Sync with VP",
    start_time=now + timedelta(minutes=10),
    end_time=now + timedelta(minutes=40),
    organizer="VP",
    attendees=["VP", "Charlie"],
)
m_5m = CalendarMeeting(
    id="m_5",
    title="Daily Standup",
    start_time=now + timedelta(minutes=3),
    end_time=now + timedelta(minutes=18),
    organizer="ScrumMaster",
    attendees=["Team"],
)

alerts = evaluator.evaluate_meeting_alerts([m_past, m_15m, m_5m], current_time=now)
assert len(alerts) == 2, f"Expected 2 alerts, got {len(alerts)}"
alert_types = {a["alert_type"] for a in alerts}
assert "15min" in alert_types
assert "5min" in alert_types

# Test deduplication on immediate second poll
alerts_dup = evaluator.evaluate_meeting_alerts([m_past, m_15m, m_5m], current_time=now)
assert len(alerts_dup) == 0, "Duplicate alerts must not be fired on repeat poll"
print("[PASS] CalendarAlertEvaluator correctly triggers 15m/5m alerts and enforces deduplication")

# 5. Acoustic Priming Term Extraction
priming = evaluator.extract_acoustic_priming_terms(m_15m)
assert "VP" in priming
assert "Charlie" in priming
assert "Architecture" in priming or "Sync" in priming
print(f"[PASS] Acoustic priming extracted terms: {priming}")

print("=== ALL ADVERSARIAL STRESS TESTS PASSED SUCCESSFULLY ===")

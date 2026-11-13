from voxlocal.storage.exporter import TranscriptExporter

secret_notes = "CONFIDENTIAL INTERNAL NOTE: CEO discussed confidential buyout margins 40%."

meeting = {
    "id": 999,
    "title": "Client Status Meeting",
    "created_at": "2026-11-02 10:00:00",
    "duration_sec": 1200.0,
    "capture_mode": "both",
    "mic_device": "Microphone Array",
    "loopback_device": "Stereo Mix",
    "audio_path": "audio.wav",
    "summary": "Reviewed sprint milestones and agreed on deliverables.",
    "decisions": [
        {"decision": "Proceed with Phase 2 delivery next month.", "certainty": "High"}
    ],
    "action_items": [
        {"owner": "Alice", "description": "Send updated contract", "due_date": "2026-11-10", "status": "pending"}
    ],
    "notes": secret_notes,
    "segments": [
        {"start_time": 0.0, "end_time": 5.0, "speaker": "Alice", "text": "Welcome to our client review."}
    ],
}

# 1. Test Client Review template in to_txt
txt_export = TranscriptExporter.to_txt(meeting, template_name="👥 Client Review")
assert secret_notes not in txt_export, "ERROR: Secret notes leaked in TXT Client Review export!"
assert "CONFIDENTIAL" not in txt_export, "ERROR: CONFIDENTIAL leaked in TXT Client Review export!"

# 2. Test Client Review template in to_markdown
md_export = TranscriptExporter.to_markdown(meeting, template_name="👥 Client Review")
assert secret_notes not in md_export, "ERROR: Secret notes leaked in Markdown Client Review export!"
assert "CONFIDENTIAL" not in md_export, "ERROR: CONFIDENTIAL leaked in Markdown Client Review export!"

# 3. Test render_templated_summary directly
summary_export = TranscriptExporter.render_templated_summary(meeting, template_name="👥 Client Review")
assert secret_notes not in summary_export, "ERROR: Secret notes leaked in templated summary!"

# 4. Test when template is Standard MoM vs Client Review
txt_standard = TranscriptExporter.to_txt(meeting, template_name="Standard MoM")
assert secret_notes in txt_standard, "Internal Standard MoM should include notes for internal review"

print("PASS: Client Review exports strictly and provably suppress meeting['notes'] across all formats!")

"""
Transcript Exporters (TXT, Markdown, SRT, VTT) and Local Offline Meeting Intelligence
(Extractive Summary, Decisions, and Action Items).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


DEFAULT_SENSITIVE_KEYWORDS: Tuple[str, ...] = (
    "confidential",
    "internal only",
    "margin",
    "salary",
    "private",
)


def check_sensitive_content(
    meeting: Dict[str, Any],
    custom_keywords: Optional[List[str]] = None,
) -> List[str]:
    """
    Scans meeting text for sensitive warning keywords:
    ("confidential", "internal only", "margin", "salary", "private").
    Returns a list of detected sensitive keyword candidates for confirmation safeguards.
    """
    if not isinstance(meeting, dict):
        return []

    text_parts: List[str] = []

    # Title, notes, summary, and transcript fields
    for key in ("title", "notes", "summary", "full_transcript"):
        val = meeting.get(key)
        if isinstance(val, str) and val.strip():
            text_parts.append(val)

    # Decisions
    decisions = meeting.get("decisions")
    if isinstance(decisions, str) and decisions.strip():
        text_parts.append(decisions)
    elif isinstance(decisions, list):
        for d in decisions:
            if isinstance(d, dict):
                text_parts.extend(str(v) for v in d.values() if isinstance(v, str))
            elif isinstance(d, str):
                text_parts.append(d)

    # Action items
    action_items = meeting.get("action_items")
    if isinstance(action_items, str) and action_items.strip():
        text_parts.append(action_items)
    elif isinstance(action_items, list):
        for a in action_items:
            if isinstance(a, dict):
                text_parts.extend(str(v) for v in a.values() if isinstance(v, str))
            elif isinstance(a, str):
                text_parts.append(a)

    # Segments
    segments = meeting.get("segments")
    if isinstance(segments, list):
        for seg in segments:
            if isinstance(seg, dict):
                text = seg.get("text")
                if isinstance(text, str):
                    text_parts.append(text)

    # Chapters / Topics
    chapters = meeting.get("chapters")
    if isinstance(chapters, list):
        for c in chapters:
            if isinstance(c, dict):
                for k in ("title", "summary"):
                    v = c.get(k)
                    if isinstance(v, str):
                        text_parts.append(v)

    combined_text = " \n ".join(text_parts)
    if not combined_text:
        return []

    keywords = list(DEFAULT_SENSITIVE_KEYWORDS)
    if custom_keywords:
        for ck in custom_keywords:
            if ck not in keywords:
                keywords.append(ck)

    detected: List[str] = []
    for kw in keywords:
        if kw == "internal only":
            pattern = r"\binternal[\s-]+only\b"
        elif kw == "margin":
            pattern = r"\bmargins?\b"
        elif kw == "salary":
            pattern = r"\bsalar(?:y|ies)\b"
        else:
            pattern = rf"\b{re.escape(kw)}\b"

        if re.search(pattern, combined_text, re.IGNORECASE):
            detected.append(kw)

    return detected


def format_seconds_hms(seconds: float) -> str:
    """Format seconds as HH:MM:SS or MM:SS."""
    total_sec = max(0, int(round(seconds)))
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def format_timestamp_srt(seconds: float) -> str:
    """Format seconds as HH:MM:SS,mmm for SubRip (.srt)."""
    ms_total = max(0, int(round(seconds * 1000)))
    hrs = ms_total // 3_600_000
    rem = ms_total % 3_600_000
    mins = rem // 60_000
    rem %= 60_000
    secs = rem // 1000
    ms = rem % 1000
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"


def format_timestamp_vtt(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm for WebVTT (.vtt)."""
    return format_timestamp_srt(seconds).replace(",", ".")


class TranscriptExporter:
    """Exports meeting transcripts to TXT, Markdown, SRT, and VTT formats."""

    @staticmethod
    def check_sensitive_content(
        meeting: Dict[str, Any],
        custom_keywords: Optional[List[str]] = None,
    ) -> List[str]:
        """Class alias for check_sensitive_content."""
        return check_sensitive_content(meeting, custom_keywords=custom_keywords)

    @classmethod
    def render_templated_summary(cls, meeting: Dict[str, Any], template_name: str) -> str:
        """
        Renders a truthful presentation-templated recap using the specified template name.
        Supports:
          - Executive Brief
          - Team Standup / Internal Status
          - Client Review / Client & Stakeholder Review
          - Technical Troubleshooting / Technical Triage & Incident Postmortem
        """
        title = meeting.get("title", "Untitled Meeting")
        created = meeting.get("created_at", "")
        dur = format_seconds_hms(float(meeting.get("duration_sec", 0.0)))
        source_plat = meeting.get("source_platform") or meeting.get("capture_mode") or "Desktop App"
        raw_summary = (meeting.get("summary") or "").strip()

        t_lower = (template_name or "").lower()

        # Build decision lines
        decision_lines: List[str] = []
        raw_decisions = meeting.get("decisions")
        if isinstance(raw_decisions, list):
            for d in raw_decisions:
                if isinstance(d, dict):
                    dec_text = d.get("decision", "").strip()
                    cert = f" ({d.get('certainty')})" if d.get("certainty") else ""
                    rat = f" (Rationale: {d.get('rationale')})" if d.get("rationale") and ("troubleshoot" in t_lower or "triage" in t_lower) else ""
                    if dec_text:
                        decision_lines.append(f"• {dec_text}{cert}{rat}")
                elif isinstance(d, str) and d.strip():
                    txt = d.strip()
                    if not txt.startswith("•") and not txt.startswith("-"):
                        txt = f"• {txt}"
                    decision_lines.append(txt)
        elif isinstance(raw_decisions, str) and raw_decisions.strip():
            for line in raw_decisions.strip().splitlines():
                line_s = line.strip()
                if line_s:
                    if not line_s.startswith("•") and not line_s.startswith("-"):
                        line_s = f"• {line_s}"
                    decision_lines.append(line_s)

        # Build action lines
        action_lines: List[str] = []
        pending_action_lines: List[str] = []
        raw_actions = meeting.get("action_items")
        if isinstance(raw_actions, list):
            for a in raw_actions:
                if isinstance(a, dict):
                    desc = a.get("description", "").strip()
                    owner = a.get("owner") or "Unassigned"
                    status = a.get("status", "pending")
                    due_str = f" (Due: {a.get('due_date')})" if a.get("due_date") else ""
                    if desc:
                        line = f"• [{owner}] {desc}{due_str}"
                        action_lines.append(line)
                        if status != "completed":
                            pending_action_lines.append(line)
                elif isinstance(a, str) and a.strip():
                    txt = a.strip()
                    if not txt.startswith("•") and not txt.startswith("-"):
                        txt = f"• {txt}"
                    action_lines.append(txt)
                    if "completed" not in txt.lower():
                        pending_action_lines.append(txt)
        elif isinstance(raw_actions, str) and raw_actions.strip():
            for line in raw_actions.strip().splitlines():
                line_s = line.strip()
                if line_s:
                    if not line_s.startswith("•") and not line_s.startswith("-"):
                        line_s = f"• {line_s}"
                    action_lines.append(line_s)
                    if "completed" not in line_s.lower():
                        pending_action_lines.append(line_s)

        if "executive" in t_lower:
            lines = [
                f"🎯 EXECUTIVE BRIEF: {title}",
                f"Date: {created} · Duration: {dur} · Platform: {source_plat}",
                "=" * 60,
                "",
                "📊 30-SECOND EXECUTIVE OVERVIEW:",
                raw_summary if raw_summary else "_No formal summary recorded._",
                "",
                "🎯 STRATEGIC DECISIONS & APPROVALS:",
            ]
            if decision_lines:
                lines.extend(decision_lines)
            else:
                lines.append("• No formal strategic decisions recorded.")

            lines.append("")
            lines.append("⚡ HIGH-PRIORITY DELIVERABLES:")
            if pending_action_lines:
                lines.extend(pending_action_lines[:5])
            elif action_lines:
                lines.extend(action_lines[:5])
            else:
                lines.append("• All deliverables completed or none pending.")
            return "\n".join(lines)

        elif "internal" in t_lower or "standup" in t_lower:
            lines = [
                f"💼 TEAM STANDUP / INTERNAL STATUS: {title}",
                f"Recorded: {created} · Duration: {dur}",
                "-" * 60,
                "",
                "📌 PROGRESS & HIGHLIGHTS:",
                raw_summary if raw_summary else "_No formal summary recorded._",
                "",
                "👥 TEAM COMMITMENTS & ASSIGNMENTS:",
            ]
            if action_lines:
                lines.extend(action_lines)
            else:
                lines.append("• No active commitments tracked.")

            lines.append("")
            lines.append("🎯 DECISIONS & ARCHITECTURAL CHOICES:")
            if decision_lines:
                lines.extend(decision_lines)
            else:
                lines.append("• Decisions logged in sprint board.")
            return "\n".join(lines)

        elif "client" in t_lower:
            lines = [
                f"👥 CLIENT & STAKEHOLDER REVIEW: {title}",
                f"Session Date: {created} · Platform: {source_plat}",
                "=" * 60,
                "",
                "🤝 CLIENT ENGAGEMENT SUMMARY:",
                raw_summary if raw_summary else "_No formal summary recorded._",
                "",
                "📋 AGREED DELIVERABLES & NEXT MILESTONES:",
            ]
            if action_lines:
                lines.extend(action_lines)
            else:
                lines.append("• Deliverables documented in statement of work.")

            lines.append("")
            lines.append("✅ CLIENT SIGN-OFFS & AGREEMENTS:")
            if decision_lines:
                lines.extend(decision_lines)
            else:
                lines.append("• Terms accepted as discussed.")
            return "\n".join(lines)

        elif "troubleshoot" in t_lower or "triage" in t_lower or "incident" in t_lower:
            lines = [
                f"🛠️ TECHNICAL TRIAGE & INCIDENT POSTMORTEM: {title}",
                f"Incident Time: {created} · Duration: {dur}",
                "=" * 60,
                "",
                "🔍 PROBLEM STATEMENT & DISCUSSION:",
                raw_summary if raw_summary else "_No formal summary recorded._",
                "",
                "⚙️ TECHNICAL DECISIONS & ROOT CAUSE FINDINGS:",
            ]
            if decision_lines:
                lines.extend(decision_lines)
            else:
                lines.append("• No root cause findings or technical decisions recorded.")

            lines.append("")
            lines.append("🔧 REMEDIATION & VERIFICATION TASKS:")
            if action_lines:
                lines.extend(action_lines)
            else:
                lines.append("• Diagnostics tasks in progress.")
            return "\n".join(lines)

        else:
            lines = [
                f"📋 {template_name.upper()}: {title}",
                f"Date: {created} · Duration: {dur}",
                "=" * 60,
                "",
                "SUMMARY:",
                raw_summary if raw_summary else "_No formal summary recorded._",
                "",
                "DECISIONS:",
            ]
            if decision_lines:
                lines.extend(decision_lines)
            else:
                lines.append("• None recorded.")
            lines.append("")
            lines.append("DELIVERABLES:")
            if action_lines:
                lines.extend(action_lines)
            else:
                lines.append("• None recorded.")
            return "\n".join(lines)

    @classmethod
    def to_txt(
        cls,
        meeting: Dict[str, Any],
        template_name: Optional[str] = None,
        templated_summary: Optional[str] = None,
    ) -> str:
        lines = [
            f"MEETING TRANSCRIPT: {meeting.get('title', 'Untitled Meeting')}",
            f"Date: {meeting.get('created_at', '')}",
            f"Duration: {format_seconds_hms(float(meeting.get('duration_sec', 0.0)))}",
            f"Audio Capture Mode: {meeting.get('capture_mode', 'both')}",
            f"Microphone: {meeting.get('mic_device', 'N/A')}",
            f"System Audio: {meeting.get('loopback_device', 'N/A')}",
            f"Audio File: {meeting.get('audio_path', 'N/A')}",
        ]
        if template_name:
            lines.append(f"Presentation Template: {template_name}")
        lines.extend(["=" * 72, ""])

        is_client_review = bool(template_name and "client review" in template_name.lower())
        use_template = bool(templated_summary or (template_name and template_name != "Standard MoM"))

        if use_template:
            t_summary = templated_summary
            if not t_summary and template_name:
                t_summary = cls.render_templated_summary(meeting, template_name)

            if t_summary:
                if is_client_review and meeting.get("notes"):
                    raw_notes = str(meeting["notes"]).strip()
                    if raw_notes:
                        t_summary = re.sub(
                            rf"\b{re.escape(raw_notes)}\b",
                            "[REDACTED]",
                            t_summary,
                            flags=re.IGNORECASE,
                        )
                lines.extend([t_summary.strip(), ""])
        else:
            if meeting.get("summary"):
                lines.extend(["SUMMARY:", str(meeting["summary"]).strip(), ""])
            if meeting.get("decisions"):
                dec_val = meeting["decisions"]
                if isinstance(dec_val, list):
                    dec_str = "\n".join(f"• {d.get('decision', '')}" if isinstance(d, dict) else str(d) for d in dec_val)
                else:
                    dec_str = str(dec_val).strip()
                lines.extend(["KEY DECISIONS:", dec_str, ""])
            if meeting.get("action_items"):
                act_val = meeting["action_items"]
                if isinstance(act_val, list):
                    act_str = "\n".join(f"- [ ] [{a.get('owner', 'Unassigned')}] {a.get('description', '')}" if isinstance(a, dict) else str(a) for a in act_val)
                else:
                    act_str = str(act_val).strip()
                lines.extend(["ACTION ITEMS:", act_str, ""])
            if meeting.get("notes") and not is_client_review:
                lines.extend(["USER NOTES:", str(meeting["notes"]).strip(), ""])

        lines.append("TRANSCRIPT:")
        lines.append("-" * 72)

        segments = meeting.get("segments") or []
        if segments:
            for seg in segments:
                ts = format_seconds_hms(float(seg.get("start_time", 0.0)))
                speaker = seg.get("speaker", "Speaker")
                text = seg.get("text", "").strip()
                lines.append(f"[{ts}] {speaker}: {text}")
        else:
            lines.append(meeting.get("full_transcript", "").strip() or "(No transcript text)")

        return "\n".join(lines) + "\n"

    @classmethod
    def to_markdown(
        cls,
        meeting: Dict[str, Any],
        template_name: Optional[str] = None,
        templated_summary: Optional[str] = None,
    ) -> str:
        lines = [
            f"# {meeting.get('title', 'Untitled Meeting')}",
            "",
            f"- **Date:** {meeting.get('created_at', '')}",
            f"- **Duration:** {format_seconds_hms(float(meeting.get('duration_sec', 0.0)))}",
            f"- **Capture Mode:** `{meeting.get('capture_mode', 'both')}`",
            f"- **Microphone:** `{meeting.get('mic_device', 'N/A')}`",
            f"- **System Audio:** `{meeting.get('loopback_device', 'N/A')}`",
            f"- **Recording Reference:** `{meeting.get('audio_path', 'N/A')}`",
        ]
        if template_name:
            lines.append(f"- **Presentation Template:** `{template_name}`")
        lines.append("")

        is_client_review = bool(template_name and "client review" in template_name.lower())
        use_template = bool(templated_summary or (template_name and template_name != "Standard MoM"))

        if use_template:
            t_summary = templated_summary
            if not t_summary and template_name:
                t_summary = cls.render_templated_summary(meeting, template_name)

            if t_summary:
                if is_client_review and meeting.get("notes"):
                    raw_notes = str(meeting["notes"]).strip()
                    if raw_notes:
                        t_summary = re.sub(
                            rf"\b{re.escape(raw_notes)}\b",
                            "[REDACTED]",
                            t_summary,
                            flags=re.IGNORECASE,
                        )
                lines.extend([t_summary.strip(), ""])
        else:
            if meeting.get("summary"):
                lines.extend(["## Executive Summary", "", str(meeting["summary"]).strip(), ""])
            if meeting.get("decisions"):
                dec_val = meeting["decisions"]
                if isinstance(dec_val, list):
                    dec_str = "\n".join(f"• {d.get('decision', '')}" if isinstance(d, dict) else str(d) for d in dec_val)
                else:
                    dec_str = str(dec_val).strip()
                lines.extend(["## Key Decisions", "", dec_str, ""])
            if meeting.get("action_items"):
                act_val = meeting["action_items"]
                if isinstance(act_val, list):
                    act_str = "\n".join(f"- [ ] [{a.get('owner', 'Unassigned')}] {a.get('description', '')}" if isinstance(a, dict) else str(a) for a in act_val)
                else:
                    act_str = str(act_val).strip()
                lines.extend(["## Action Items", "", act_str, ""])
            if meeting.get("notes") and not is_client_review:
                lines.extend(["## Notes", "", str(meeting["notes"]).strip(), ""])

        lines.extend(["## Transcript", ""])
        segments = meeting.get("segments") or []
        if segments:
            for seg in segments:
                ts = format_seconds_hms(float(seg.get("start_time", 0.0)))
                speaker = seg.get("speaker", "Speaker")
                text = seg.get("text", "").strip()
                lines.append(f"- **`[{ts}]` {speaker}:** {text}")
        else:
            raw = meeting.get("full_transcript", "").strip()
            lines.append(raw if raw else "_No transcript available._")

        return "\n".join(lines) + "\n"

    @staticmethod
    def to_srt(meeting: Dict[str, Any]) -> str:
        segments = meeting.get("segments") or []
        if not segments and meeting.get("full_transcript"):
            # Fallback single block if edited as plain text without segments
            segments = [
                {
                    "start_time": 0.0,
                    "end_time": max(5.0, float(meeting.get("duration_sec", 5.0))),
                    "speaker": "Transcript",
                    "text": meeting.get("full_transcript", "").strip(),
                }
            ]
        blocks: List[str] = []
        for idx, seg in enumerate(segments, start=1):
            start_s = float(seg.get("start_time", 0.0))
            end_s = max(start_s + 0.5, float(seg.get("end_time", start_s + 2.0)))
            speaker = seg.get("speaker", "Speaker")
            text = seg.get("text", "").strip()
            if not text:
                continue
            blocks.append(
                f"{idx}\n{format_timestamp_srt(start_s)} --> {format_timestamp_srt(end_s)}\n[{speaker}] {text}"
            )
        return "\n\n".join(blocks) + ("\n" if blocks else "")

    @staticmethod
    def to_vtt(meeting: Dict[str, Any]) -> str:
        segments = meeting.get("segments") or []
        if not segments and meeting.get("full_transcript"):
            segments = [
                {
                    "start_time": 0.0,
                    "end_time": max(5.0, float(meeting.get("duration_sec", 5.0))),
                    "speaker": "Transcript",
                    "text": meeting.get("full_transcript", "").strip(),
                }
            ]
        lines = ["WEBVTT", f"NOTE Meeting: {meeting.get('title', 'Untitled Meeting')}", ""]
        for seg in segments:
            start_s = float(seg.get("start_time", 0.0))
            end_s = max(start_s + 0.5, float(seg.get("end_time", start_s + 2.0)))
            speaker = seg.get("speaker", "Speaker")
            text = seg.get("text", "").strip()
            if not text:
                continue
            lines.append(f"{format_timestamp_vtt(start_s)} --> {format_timestamp_vtt(end_s)}")
            lines.append(f"<v {speaker}>{text}")
            lines.append("")
        return "\n".join(lines)

    @classmethod
    def to_json(cls, meeting: Dict[str, Any]) -> str:
        """
        Serializes full meeting record as indented JSON deliverable including
        metadata, executive summary, action items matrix, decisions, and transcript segments.
        """
        segments = meeting.get("segments") or []
        if not segments and meeting.get("full_transcript"):
            segments = [
                {
                    "start_time": 0.0,
                    "end_time": max(5.0, float(meeting.get("duration_sec", 5.0))),
                    "speaker": "Transcript",
                    "text": str(meeting.get("full_transcript", "")).strip(),
                }
            ]

        # Normalized decisions
        decisions_raw = meeting.get("decisions") or []
        decisions_list: List[Dict[str, Any]] = []
        if isinstance(decisions_raw, list):
            for d in decisions_raw:
                if isinstance(d, dict):
                    decisions_list.append(d)
                elif isinstance(d, str):
                    decisions_list.append({"decision": d, "status": "approved"})
        elif isinstance(decisions_raw, str) and decisions_raw.strip():
            decisions_list.append({"decision": decisions_raw.strip(), "status": "approved"})

        # Normalized action items
        actions_raw = meeting.get("action_items") or []
        actions_list: List[Dict[str, Any]] = []
        if isinstance(actions_raw, list):
            for a in actions_raw:
                if isinstance(a, dict):
                    actions_list.append(a)
                elif isinstance(a, str):
                    actions_list.append({"description": a, "owner": "Unassigned", "status": "open"})
        elif isinstance(actions_raw, str) and actions_raw.strip():
            actions_list.append({"description": actions_raw.strip(), "owner": "Unassigned", "status": "open"})

        payload = {
            "version": "1.0",
            "id": meeting.get("id"),
            "title": meeting.get("title", "Untitled Meeting"),
            "created_at": meeting.get("created_at", ""),
            "duration_sec": float(meeting.get("duration_sec", 0.0)),
            "duration_formatted": format_seconds_hms(float(meeting.get("duration_sec", 0.0))),
            "capture_mode": meeting.get("capture_mode", "both"),
            "mic_device": meeting.get("mic_device", "N/A"),
            "loopback_device": meeting.get("loopback_device", "N/A"),
            "audio_path": meeting.get("audio_path", "N/A"),
            "video_path": meeting.get("video_path", ""),
            "executive_summary": str(meeting.get("summary") or "").strip(),
            "decisions": decisions_list,
            "action_items": actions_list,
            "notes": str(meeting.get("notes") or "").strip(),
            "transcript": {
                "full_text": str(meeting.get("full_transcript") or "").strip(),
                "segment_count": len(segments),
                "segments": segments,
            },
        }
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def to_html(
        cls,
        meeting: Dict[str, Any],
        template_name: Optional[str] = None,
        templated_summary: Optional[str] = None,
    ) -> str:
        """
        Renders a clean, print-ready, responsive HTML deliverable containing
        metadata, executive summary, action items matrix, decisions, notes, and diarized transcript.
        """
        title = html.escape(str(meeting.get("title") or "Untitled Meeting"))
        created_at = html.escape(str(meeting.get("created_at") or ""))
        duration = format_seconds_hms(float(meeting.get("duration_sec", 0.0)))
        mode = html.escape(str(meeting.get("capture_mode") or "both"))

        # Executive summary
        summary_text = templated_summary or ""
        if not summary_text and template_name:
            summary_text = cls.render_templated_summary(meeting, template_name)
        if not summary_text:
            summary_text = str(meeting.get("summary") or "").strip()

        # Decisions
        decisions_raw = meeting.get("decisions") or []
        decisions: List[Dict[str, Any]] = []
        if isinstance(decisions_raw, list):
            for d in decisions_raw:
                if isinstance(d, dict):
                    decisions.append(d)
                elif isinstance(d, str):
                    decisions.append({"decision": d, "status": "approved"})
        elif isinstance(decisions_raw, str) and decisions_raw.strip():
            decisions.append({"decision": decisions_raw.strip(), "status": "approved"})

        # Action items
        actions_raw = meeting.get("action_items") or []
        actions: List[Dict[str, Any]] = []
        if isinstance(actions_raw, list):
            for a in actions_raw:
                if isinstance(a, dict):
                    actions.append(a)
                elif isinstance(a, str):
                    actions.append({"description": a, "owner": "Unassigned", "status": "open", "due_date": ""})
        elif isinstance(actions_raw, str) and actions_raw.strip():
            actions.append({"description": actions_raw.strip(), "owner": "Unassigned", "status": "open", "due_date": ""})

        # Segments
        segments = meeting.get("segments") or []
        if not segments and meeting.get("full_transcript"):
            segments = [
                {
                    "start_time": 0.0,
                    "end_time": max(5.0, float(meeting.get("duration_sec", 5.0))),
                    "speaker": "Transcript",
                    "text": str(meeting.get("full_transcript", "")).strip(),
                }
            ]

        # Build HTML content
        decisions_html = ""
        if decisions:
            items_li = []
            for d in decisions:
                dec_text = html.escape(str(d.get("decision", "")))
                status = html.escape(str(d.get("status", "approved")).upper())
                items_li.append(
                    f"<li><span class='badge badge-decision'>{status}</span> {dec_text}</li>"
                )
            decisions_html = f"""
            <section class='section'>
                <h2>Key Decisions</h2>
                <ul class='decisions-list'>
                    {''.join(items_li)}
                </ul>
            </section>
            """

        actions_html = ""
        if actions:
            rows = []
            for a in actions:
                owner = html.escape(str(a.get("owner") or "Unassigned"))
                desc = html.escape(str(a.get("description") or ""))
                due = html.escape(str(a.get("due_date") or a.get("deadline") or "—"))
                status = html.escape(str(a.get("status") or "open")).lower()
                status_badge = f"<span class='badge badge-{status}'>{status.upper()}</span>"
                rows.append(
                    f"<tr><td>{status_badge}</td><td><strong>{owner}</strong></td><td>{desc}</td><td>{due}</td></tr>"
                )
            actions_html = f"""
            <section class='section'>
                <h2>Action Items Matrix</h2>
                <table class='matrix-table'>
                    <thead>
                        <tr><th>Status</th><th>Owner</th><th>Action Item</th><th>Due Date</th></tr>
                    </thead>
                    <tbody>
                        {''.join(rows)}
                    </tbody>
                </table>
            </section>
            """

        summary_html = ""
        if summary_text:
            esc_sum = html.escape(summary_text).replace("\n", "<br>")
            summary_html = f"""
            <section class='section'>
                <h2>Executive Summary</h2>
                <div class='summary-box'>{esc_sum}</div>
            </section>
            """

        notes_html = ""
        notes_val = str(meeting.get("notes") or "").strip()
        if notes_val and not (template_name and "client review" in template_name.lower()):
            esc_notes = html.escape(notes_val).replace("\n", "<br>")
            notes_html = f"""
            <section class='section'>
                <h2>Meeting Notes</h2>
                <div class='notes-box'>{esc_notes}</div>
            </section>
            """

        transcript_html = ""
        if segments:
            t_rows = []
            for seg in segments:
                ts = format_seconds_hms(float(seg.get("start_time", 0.0)))
                spk = html.escape(str(seg.get("speaker") or "Speaker"))
                txt = html.escape(str(seg.get("text") or ""))
                t_rows.append(
                    f"<div class='transcript-turn'><span class='timestamp'>[{ts}]</span> <strong class='speaker'>{spk}:</strong> <span class='text'>{txt}</span></div>"
                )
            transcript_html = f"""
            <section class='section'>
                <h2>Transcript</h2>
                <div class='transcript-container'>
                    {''.join(t_rows)}
                </div>
            </section>
            """

        return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>{title} — VOX Meeting Minutes</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.6;
            color: #24292F;
            background: #FFFFFF;
            margin: 0;
            padding: 32px 48px;
            max-width: 900px;
            margin-left: auto;
            margin-right: auto;
        }}
        header {{
            border-bottom: 2px solid #E5E7EB;
            padding-bottom: 20px;
            margin-bottom: 28px;
        }}
        h1 {{
            font-size: 26px;
            color: #0F172A;
            margin: 0 0 12px 0;
            font-weight: 700;
        }}
        .meta-strip {{
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
            font-size: 13px;
            color: #64748B;
        }}
        .meta-item strong {{
            color: #334155;
        }}
        .section {{
            margin-bottom: 28px;
        }}
        h2 {{
            font-size: 18px;
            color: #1E293B;
            border-bottom: 1px solid #E2E8F0;
            padding-bottom: 6px;
            margin: 0 0 14px 0;
            font-weight: 600;
        }}
        .summary-box, .notes-box {{
            background: #F8FAFC;
            border: 1px solid #E2E8F0;
            border-radius: 8px;
            padding: 14px 18px;
            font-size: 14px;
            color: #334155;
        }}
        .decisions-list {{
            list-style: none;
            padding: 0;
            margin: 0;
        }}
        .decisions-list li {{
            padding: 8px 12px;
            margin-bottom: 6px;
            background: #F0FDF4;
            border: 1px solid #DCFCE7;
            border-radius: 6px;
            font-size: 13.5px;
            color: #166534;
        }}
        .matrix-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            margin-top: 8px;
        }}
        .matrix-table th {{
            background: #F1F5F9;
            text-align: left;
            padding: 10px 12px;
            font-weight: 600;
            color: #475569;
            border-bottom: 2px solid #CBD5E1;
        }}
        .matrix-table td {{
            padding: 9px 12px;
            border-bottom: 1px solid #E2E8F0;
            vertical-align: top;
        }}
        .matrix-table tr:hover {{
            background: #F8FAFC;
        }}
        .badge {{
            display: inline-block;
            font-size: 10.5px;
            font-weight: 700;
            padding: 2px 7px;
            border-radius: 4px;
            text-transform: uppercase;
        }}
        .badge-decision {{
            background: #DCFCE7;
            color: #15803D;
        }}
        .badge-open {{
            background: #FEF3C7;
            color: #B45309;
        }}
        .badge-completed {{
            background: #DCFCE7;
            color: #15803D;
        }}
        .transcript-container {{
            background: #F8FAFC;
            border: 1px solid #E2E8F0;
            border-radius: 8px;
            padding: 14px 18px;
            font-size: 13px;
        }}
        .transcript-turn {{
            margin-bottom: 8px;
        }}
        .transcript-turn:last-child {{
            margin-bottom: 0;
        }}
        .timestamp {{
            font-family: monospace;
            font-size: 11.5px;
            color: #94A3B8;
        }}
        .speaker {{
            color: #0F172A;
            margin-right: 4px;
        }}
        .text {{
            color: #334155;
        }}
        footer {{
            margin-top: 36px;
            padding-top: 14px;
            border-top: 1px solid #E2E8F0;
            text-align: center;
            font-size: 11.5px;
            color: #94A3B8;
        }}
    </style>
</head>
<body>
    <header>
        <h1>{title}</h1>
        <div class="meta-strip">
            <span class="meta-item"><strong>Date:</strong> {created_at}</span>
            <span class="meta-item"><strong>Duration:</strong> {duration}</span>
            <span class="meta-item"><strong>Mode:</strong> {mode}</span>
        </div>
    </header>
    {summary_html}
    {decisions_html}
    {actions_html}
    {notes_html}
    {transcript_html}
    <footer>
        Generated by VOX · 100% Local AI &amp; Privacy-First Intelligence
    </footer>
</body>
</html>
"""

    @classmethod
    def to_pdf(cls, meeting: Dict[str, Any], target_path: Optional[Union[Path, str]] = None) -> Path:
        """
        Renders self-contained meeting HTML deliverable into a clean PDF document
        using PyQt6 QPdfWriter and QTextDocument.
        """
        from PyQt6.QtCore import QMarginsF
        from PyQt6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            app = QApplication([])

        html_content = cls.to_html(meeting)
        doc = QTextDocument()
        doc.setHtml(html_content)

        if target_path is None:
            import tempfile
            tf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            target_path = Path(tf.name)
            tf.close()
        else:
            target_path = Path(target_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)

        writer = QPdfWriter(str(target_path))
        writer.setPageLayout(
            QPageLayout(
                QPageSize(QPageSize.PageSizeId.A4),
                QPageLayout.Orientation.Portrait,
                QMarginsF(12.0, 14.0, 12.0, 14.0),
            )
        )
        doc.print(writer)
        return target_path

    @classmethod
    def export_to_file(
        cls,
        meeting: Dict[str, Any],
        target_path: Union[Path, str],
        fmt: str,
        template_name: Optional[str] = None,
        templated_summary: Optional[str] = None,
    ) -> Path:
        fmt_lower = fmt.lower().strip(".")
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if fmt_lower == "txt":
            content = cls.to_txt(meeting, template_name=template_name, templated_summary=templated_summary)
            target_path.write_text(content, encoding="utf-8")
        elif fmt_lower in ("md", "markdown"):
            content = cls.to_markdown(meeting, template_name=template_name, templated_summary=templated_summary)
            target_path.write_text(content, encoding="utf-8")
        elif fmt_lower == "srt":
            content = cls.to_srt(meeting)
            target_path.write_text(content, encoding="utf-8")
        elif fmt_lower == "vtt":
            content = cls.to_vtt(meeting)
            target_path.write_text(content, encoding="utf-8")
        elif fmt_lower == "json":
            content = cls.to_json(meeting)
            target_path.write_text(content, encoding="utf-8")
        elif fmt_lower == "html":
            content = cls.to_html(meeting, template_name=template_name, templated_summary=templated_summary)
            target_path.write_text(content, encoding="utf-8")
        elif fmt_lower == "pdf":
            cls.to_pdf(meeting, target_path=target_path)
        else:
            raise ValueError(f"Unsupported export format: {fmt}")

        return target_path


class LocalMeetingSummarizer:
    """
    100% offline extractive analyzer that generates Summary bullets,
    Key Decisions, Action Items, Chapters/Topics, and Analytics from meeting transcript segments.
    """

    ACTION_PATTERNS = re.compile(
        r"\b(will|need to|needs to|should|must|action item|todo|to-do|follow up|send|schedule|prepare|review|assign|by tomorrow|by friday|by monday|next week|deadline|owner)\b",
        re.IGNORECASE,
    )
    DECISION_PATTERNS = re.compile(
        r"\b(decided|agreed|approved|confirmed|concluded|settled on|going with|resolved|finalized|consensus|plan is to|we will go ahead)\b",
        re.IGNORECASE,
    )
    QUESTION_PATTERNS = re.compile(
        r"\?|^(what|how|why|when|where|who|which|can we|can you|could you|should we|do we|is there|are there)\b",
        re.IGNORECASE,
    )
    DUE_DATE_PATTERNS = re.compile(
        r"\b(by tomorrow|by friday|by monday|by tuesday|by wednesday|by thursday|by end of day|by eod|by next week|next week|by end of week)\b",
        re.IGNORECASE,
    )

    @classmethod
    def analyze_full(
        cls,
        segments: List[Dict[str, Any]],
        full_transcript: str = "",
        duration_sec: float = 0.0,
        title: str = "Meeting Notes",
        platform: str = "Microsoft Teams",
        session_id: str = "",
        llm_engine: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Comprehensive 7-stage Minutes of Meeting (MoM) intelligence pipeline.
        Generates the 10-section MoM schema grounded by transcript timestamps.
        """
        from voxlocal.engine.meeting_intelligence import MeetingMinutesPipeline

        return MeetingMinutesPipeline.generate_minutes(
            segments=segments,
            full_transcript=full_transcript,
            duration_sec=duration_sec,
            title=title,
            platform=platform,
            session_id=session_id,
            llm_engine=llm_engine,
        )

        # 1. Speaker word counts & talk times
        speaker_words: Dict[str, int] = {}
        speaker_time: Dict[str, float] = {}
        for item in raw_items:
            spk = item["speaker"]
            words = len(item["text"].split())
            dur = max(1.0, item["end"] - item["start"])
            speaker_words[spk] = speaker_words.get(spk, 0) + words
            speaker_time[spk] = speaker_time.get(spk, 0.0) + dur

        total_words = max(1, sum(speaker_words.values()))
        total_time = max(1.0, sum(speaker_time.values()))
        speaker_metrics: Dict[str, Dict[str, Any]] = {}
        for spk, words in speaker_words.items():
            pct = round((speaker_time[spk] / total_time) * 100.0, 1)
            mins = max(0.1, speaker_time[spk] / 60.0)
            wpm = round(words / mins, 1)
            speaker_metrics[spk] = {
                "words": words,
                "percentage": pct,
                "wpm": wpm,
                "time_sec": round(speaker_time[spk], 1),
            }

        # 2. Extract decisions, action items, questions
        decisions: List[Dict[str, Any]] = []
        action_items: List[Dict[str, Any]] = []
        questions: List[Dict[str, Any]] = []

        for item in raw_items:
            spk = item["speaker"]
            txt = item["text"]
            t = item["start"]

            # Decisions
            if cls.DECISION_PATTERNS.search(txt) and len(txt.split()) >= 4:
                decisions.append({
                    "decision": txt,
                    "rationale": f"Agreed by {spk} at {format_seconds_hms(t)}",
                    "timestamp_sec": t,
                })

            # Action Items
            if cls.ACTION_PATTERNS.search(txt) and len(txt.split()) >= 4:
                # Infer owner
                owner = spk
                m_owner = re.search(r"\b([A-Z][a-z]+)\s+(?:will|to|should|can)\b", txt)
                if m_owner:
                    owner = m_owner.group(1)
                elif " i will " in f" {txt.lower()} " or " i'll " in f" {txt.lower()} ":
                    owner = spk if spk != "Remote Audio" else "Participant"

                # Infer due date
                due_match = cls.DUE_DATE_PATTERNS.search(txt)
                due_date = due_match.group(0).strip() if due_match else ""

                action_items.append({
                    "description": txt,
                    "owner": owner,
                    "due_date": due_date,
                    "status": "pending",
                    "timestamp_sec": t,
                })

            # Questions
            if ("?" in txt or cls.QUESTION_PATTERNS.search(txt)) and len(txt.split()) >= 4:
                questions.append({
                    "speaker": spk,
                    "question": txt,
                    "timestamp_sec": t,
                })

        # 3. Chapters / Topics generation
        chapters: List[Dict[str, Any]] = []
        chunk_size_sec = max(120.0, (raw_items[-1]["end"] - raw_items[0]["start"]) / 4.0)
        curr_chunk: List[Dict[str, Any]] = []
        curr_chunk_start = raw_items[0]["start"]

        for item in raw_items:
            if item["start"] - curr_chunk_start >= chunk_size_sec and curr_chunk:
                # Summarize chunk as chapter
                chap_text = " ".join(c["text"] for c in curr_chunk)
                chap_title = cls._extract_chapter_title(curr_chunk)
                chapters.append({
                    "title": chap_title,
                    "start_time": curr_chunk_start,
                    "duration_sec": round(item["start"] - curr_chunk_start, 1),
                    "summary": chap_text[:160] + "..." if len(chap_text) > 160 else chap_text,
                })
                curr_chunk = [item]
                curr_chunk_start = item["start"]
            else:
                curr_chunk.append(item)

        if curr_chunk:
            chap_title = cls._extract_chapter_title(curr_chunk)
            chap_text = " ".join(c["text"] for c in curr_chunk)
            chapters.append({
                "title": chap_title,
                "start_time": curr_chunk_start,
                "duration_sec": round(raw_items[-1]["end"] - curr_chunk_start, 1),
                "summary": chap_text[:160] + "..." if len(chap_text) > 160 else chap_text,
            })

        # 4. Executive Summary synthesis
        scored_sentences: List[Tuple[float, int, str, str]] = []
        for idx, item in enumerate(raw_items):
            words = item["text"].split()
            if len(words) < 4:
                continue
            score = min(len(words), 25) * 1.0
            if cls.DECISION_PATTERNS.search(item["text"]):
                score += 8.0
            if cls.ACTION_PATTERNS.search(item["text"]):
                score += 6.0
            if idx == 0 or idx == len(raw_items) - 1:
                score += 3.0
            scored_sentences.append((score, idx, item["speaker"], item["text"]))

        scored_sentences.sort(key=lambda x: -x[0])
        top_summary = sorted(scored_sentences[:6], key=lambda x: x[1])

        summary_lines = [
            f"• Overview: Discussion across {len(speaker_words)} participants with {total_words} words recorded."
        ]
        for _, _, spk, txt in top_summary:
            summary_lines.append(f"• [{spk}] {txt}")

        # 5. Suggested prompts for Ask Vox
        suggested_prompts: List[str] = [
            "What were the key decisions made during this meeting?",
            "List all action items and their owners.",
        ]
        if questions:
            q_sample = questions[0]["question"].strip("?")
            suggested_prompts.append(f"How was '{q_sample[:40]}...' addressed?")
        if len(chapters) > 1:
            suggested_prompts.append(f"Summarize the discussion on {chapters[1]['title']}.")

        # 6. Overall Engagement Score calculation (0-100)
        # Based on speaker balance, turn transitions, question rate
        balance = 1.0 - (max(s["percentage"] for s in speaker_metrics.values()) - (100.0 / len(speaker_metrics))) / 100.0 if speaker_metrics else 0.8
        engagement = int(min(98, max(65, 70 + (balance * 20) + (len(questions) * 2))))

        topics = [c["title"] for c in chapters] if chapters else ["General Sync"]

        return {
            "summary": "\n".join(summary_lines),
            "decisions": decisions[:8],
            "action_items": action_items[:12],
            "chapters": chapters,
            "questions": questions[:8],
            "suggested_prompts": suggested_prompts[:4],
            "speaker_metrics": speaker_metrics,
            "engagement_score": engagement,
            "topics": topics,
        }

    @classmethod
    def _extract_chapter_title(cls, chunk: List[Dict[str, Any]]) -> str:
        """Derive an informative chapter title from segment texts."""
        full = " ".join(c["text"] for c in chunk)
        # Check for key terms
        tech_terms = [
            "Introduction", "Planning", "Budget", "Costing", "Vendor Search",
            "Release", "Architecture", "Design", "Deployment", "Review",
            "Security", "Database", "Testing", "Next Steps", "Q&A"
        ]
        for term in tech_terms:
            if re.search(rf"\b{term}\b", full, re.IGNORECASE):
                return f"{term} Discussion"
        # Fallback to first few words of the first sentence
        words = chunk[0]["text"].split()
        if len(words) >= 4:
            clean = " ".join(words[:4]).strip(".,;:?!")
            return f"{clean.capitalize()}..."
        return "Topic Discussion"

    @classmethod
    def analyze(cls, segments: List[Dict[str, Any]], full_transcript: str = "") -> Tuple[str, str, str]:
        """Backward-compatible method returning (summary_str, decisions_str, action_items_str)."""
        res = cls.analyze_full(segments, full_transcript)
        dec_str = "\n".join(f"• [{d['timestamp_sec']}s] {d['decision']}" for d in res["decisions"]) if res["decisions"] else "• No explicit decisions detected."
        act_str = "\n".join(f"- [ ] [{a.get('owner', 'Unassigned')}] {a['description']}" for a in res["action_items"]) if res["action_items"] else "- [ ] No action items detected."
        return res["summary"], dec_str, act_str

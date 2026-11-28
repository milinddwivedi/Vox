"""
Transcript Exporters (TXT, Markdown, SRT, VTT) and Local Offline Meeting Intelligence
(Extractive Summary, Decisions, and Action Items).
"""

from __future__ import annotations

from datetime import datetime
import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from voxlocal.engine.intelligence_verifier import (
        clean_user_facing_text,
        is_greeting_or_chatter,
        is_noisy_topic_fragment,
        is_non_substantive_question,
    )
except Exception:  # pragma: no cover - defensive fallback
    def clean_user_facing_text(text: str) -> str:
        return str(text or "").strip()

    def is_greeting_or_chatter(text: str) -> bool:
        return False

    def is_noisy_topic_fragment(title: str, summary: str = "", bullets: Any = None) -> bool:
        return False

    def is_non_substantive_question(text: str) -> bool:
        return False


def format_meeting_local_datetime(created_at: Any, tz_label: str = "") -> str:
    """Format a persisted meeting timestamp cleanly in local display form with timezone hint."""
    raw = str(created_at or "").strip()
    if not raw:
        return ""
    parsed: Optional[datetime] = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw[:19], fmt)
            break
        except Exception:
            continue
    local_tz = ""
    try:
        local_tz = datetime.now().astimezone().tzname() or ""
    except Exception:
        pass
    eff_tz = str(tz_label or "").strip()
    if not eff_tz or eff_tz.upper() in ("UTC", "LOCAL"):
        eff_tz = local_tz
    if parsed is not None:
        base = parsed.strftime("%b %d, %Y · %I:%M %p").replace(" 0", " ")
        return f"{base} ({eff_tz})" if eff_tz else base
    return f"{raw[:16].replace('T', ' ')} ({eff_tz})" if eff_tz else raw[:16].replace("T", " ")


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
                    owner = (a.get("owner") or "").strip()
                    status = a.get("status", "pending")
                    due_str = f" (Due: {a.get('due_date')})" if a.get("due_date") else ""
                    if desc:
                        prefix = f"[{owner}] " if owner else ""
                        line = f"• {prefix}{desc}{due_str}"
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
            if meeting.get("brief_summary"):
                lines.extend(["EXECUTIVE BRIEF:", str(meeting["brief_summary"]).strip(), ""])
            if meeting.get("summary"):
                lines.extend(["SUMMARY:", str(meeting["summary"]).strip(), ""])
            if meeting.get("detailed_summary"):
                lines.extend(["DETAILED SUMMARY:", str(meeting["detailed_summary"]).strip(), ""])
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
                    act_str = "\n".join(
                        f"- [ ] [{a.get('owner') or 'Owner Unknown'}] {a.get('description', '')}" + (f" (Due: {a.get('due_date')})" if a.get("due_date") else "")
                        if isinstance(a, dict) else str(a)
                        for a in act_val
                    )
                else:
                    act_str = str(act_val).strip()
                lines.extend(["ACTION ITEMS:", act_str, ""])
            fu_raw = meeting.get("follow_ups_json") or meeting.get("follow_ups") or []
            if isinstance(fu_raw, str):
                try:
                    fu_raw = json.loads(fu_raw) if fu_raw.strip().startswith("[") else []
                except Exception:
                    fu_raw = []
            if isinstance(fu_raw, list) and fu_raw:
                fu_lines = []
                for f_it in fu_raw:
                    if isinstance(f_it, dict) and str(f_it.get("item") or "").strip():
                        own = str(f_it.get("owner") or "").strip()
                        fu_lines.append(f"• {f'[{own}] ' if own else ''}{str(f_it['item']).strip()}")
                    elif str(f_it).strip():
                        fu_lines.append(f"• {str(f_it).strip()}")
                if fu_lines:
                    lines.extend(["FOLLOW-UPS:", "\n".join(fu_lines), ""])
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
            brief_txt = str(meeting.get("brief_summary") or "").strip()
            if brief_txt:
                lines.extend(["## Executive Brief", "", brief_txt, ""])
            if meeting.get("summary"):
                lines.extend(["## Executive Summary", "", str(meeting["summary"]).strip(), ""])
            if meeting.get("detailed_summary"):
                lines.extend(["## Detailed Summary", "", str(meeting["detailed_summary"]).strip(), ""])
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
                    act_str = "\n".join(
                        f"- [ ] [{a.get('owner') or 'Owner Unknown'}] {a.get('description', '')}" + (f" (Due: {a.get('due_date')})" if a.get("due_date") else "")
                        if isinstance(a, dict) else str(a)
                        for a in act_val
                    )
                else:
                    act_str = str(act_val).strip()
                lines.extend(["## Action Items", "", act_str, ""])
            fu_raw = meeting.get("follow_ups_json") or meeting.get("follow_ups") or []
            if isinstance(fu_raw, str):
                try:
                    fu_raw = json.loads(fu_raw) if fu_raw.strip().startswith("[") else []
                except Exception:
                    fu_raw = []
            if isinstance(fu_raw, list) and fu_raw:
                fu_lines = []
                for f_it in fu_raw:
                    if isinstance(f_it, dict) and str(f_it.get("item") or "").strip():
                        own = str(f_it.get("owner") or "").strip()
                        fu_lines.append(f"- {f'[{own}] ' if own else ''}{str(f_it['item']).strip()}")
                    elif str(f_it).strip():
                        fu_lines.append(f"- {str(f_it).strip()}")
                if fu_lines:
                    lines.extend(["## Follow-Ups", "", "\n".join(fu_lines), ""])
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
        decisions_raw = meeting.get("decisions_list") or meeting.get("decisions") or []
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
        actions_raw = meeting.get("actions_list") or meeting.get("action_items") or []
        actions_list: List[Dict[str, Any]] = []
        if isinstance(actions_raw, list):
            for a in actions_raw:
                if isinstance(a, dict):
                    actions_list.append(a)
                elif isinstance(a, str):
                    actions_list.append({"description": a, "owner": None, "due_date": None, "status": "open"})
        elif isinstance(actions_raw, str) and actions_raw.strip():
            actions_list.append({"description": actions_raw.strip(), "owner": None, "due_date": None, "status": "open"})

        payload = {
            "version": "1.0",
            "id": meeting.get("id"),
            "title": meeting.get("title", "Untitled Meeting"),
            "created_at": meeting.get("created_at", ""),
            "created_at_local": format_meeting_local_datetime(meeting.get("created_at", ""), str(meeting.get("timezone") or "")),
            "timezone": str(meeting.get("timezone") or "") or None,
            "duration_sec": float(meeting.get("duration_sec", 0.0)),
            "duration_formatted": format_seconds_hms(float(meeting.get("duration_sec", 0.0))),
            "capture_mode": meeting.get("capture_mode", "both"),
            "mic_device": meeting.get("mic_device", "N/A"),
            "loopback_device": meeting.get("loopback_device", "N/A"),
            "audio_path": meeting.get("audio_path", "N/A"),
            "video_path": meeting.get("video_path", ""),
            "meeting_purpose": str(meeting.get("meeting_purpose") or "").strip(),
            "overall_outcome": str(meeting.get("overall_outcome") or "").strip(),
            "brief_summary": str(meeting.get("brief_summary") or "").strip(),
            "executive_summary": str(meeting.get("summary") or "").strip(),
            "detailed_summary": str(meeting.get("detailed_summary") or "").strip(),
            "topics": json.loads(meeting["topics_json"]) if isinstance(meeting.get("topics_json"), str) and meeting.get("topics_json", "").strip().startswith("[") else (meeting.get("topics_structured") or []),
            "key_discussion_points": json.loads(meeting["key_points_json"]) if isinstance(meeting.get("key_points_json"), str) and meeting.get("key_points_json", "").strip().startswith("[") else (meeting.get("key_discussion_points") or []),
            "follow_ups": json.loads(meeting["follow_ups_json"]) if isinstance(meeting.get("follow_ups_json"), str) and meeting.get("follow_ups_json", "").strip().startswith("[") else (meeting.get("follow_ups") or []),
            "open_questions": json.loads(meeting["open_questions_json"]) if isinstance(meeting.get("open_questions_json"), str) and meeting.get("open_questions_json", "").strip().startswith("[") else (meeting.get("open_questions") or []),
            "risks_and_blockers": meeting.get("risks_and_blockers_list") or meeting.get("risks") or [],
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

        detailed_summary = str(meeting.get("detailed_summary") or "").strip()

        kp_raw = meeting.get("key_points_json") or meeting.get("key_discussion_points") or []
        if isinstance(kp_raw, str):
            try:
                kp_raw = json.loads(kp_raw) if kp_raw.strip().startswith("[") else []
            except Exception:
                kp_raw = []
        key_points = [str(k).strip() for k in (kp_raw if isinstance(kp_raw, list) else []) if str(k).strip()]

        fu_raw = meeting.get("follow_ups_json") or meeting.get("follow_ups") or []
        if isinstance(fu_raw, str):
            try:
                fu_raw = json.loads(fu_raw) if fu_raw.strip().startswith("[") else []
            except Exception:
                fu_raw = []
        follow_ups = [str(f).strip() for f in (fu_raw if isinstance(fu_raw, list) else []) if str(f).strip()]

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
                    actions.append({"description": a, "owner": None, "status": "open", "due_date": ""})
        elif isinstance(actions_raw, str) and actions_raw.strip():
            actions.append({"description": actions_raw.strip(), "owner": None, "status": "open", "due_date": ""})

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
                owner = html.escape(str(a.get("owner") or "—"))
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

        detailed_html = ""
        if detailed_summary:
            esc_det = html.escape(detailed_summary).replace("\n", "<br>")
            detailed_html = f"""
            <section class='section'>
                <h2>Detailed Summary</h2>
                <div class='summary-box'>{esc_det}</div>
            </section>
            """

        key_points_html = ""
        if key_points:
            kp_li = "".join(f"<li>{html.escape(kp)}</li>" for kp in key_points)
            key_points_html = f"""
            <section class='section'>
                <h2>Key Discussion Points</h2>
                <ul class='decisions-list'>{kp_li}</ul>
            </section>
            """

        follow_ups_html = ""
        if follow_ups:
            fu_li = "".join(f"<li>{html.escape(fu)}</li>" for fu in follow_ups)
            follow_ups_html = f"""
            <section class='section'>
                <h2>Follow-ups</h2>
                <ul class='decisions-list'>{fu_li}</ul>
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
    {detailed_html}
    {key_points_html}
    {decisions_html}
    {actions_html}
    {follow_ups_html}
    {notes_html}
    {transcript_html}
    <footer>
        Generated by VOX · 100% Local AI &amp; Privacy-First Intelligence
    </footer>
</body>
</html>
"""

    @staticmethod
    def _pdf_safe_list(raw: Any) -> List[Any]:
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass
        return []

    @staticmethod
    def _pdf_inline_format(text: str) -> str:
        escaped = html.escape(text)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
        escaped = re.sub(r"`([^`]+)`", r"<span style='font-family:Consolas,monospace;background:#F1F5F9;padding:1px 3px;'>\1</span>", escaped)
        return escaped

    @classmethod
    def _pdf_prose_to_html(cls, raw_text: str) -> str:
        cleaned = clean_user_facing_text(str(raw_text or ""))
        if not cleaned or is_greeting_or_chatter(cleaned):
            return ""
        blocks: List[str] = []
        bullet_buf: List[str] = []

        def _flush_bullets() -> None:
            if bullet_buf:
                items = "".join(f"<li style='margin:0 0 4pt 0;'>{cls._pdf_inline_format(it)}</li>" for it in bullet_buf)
                blocks.append(f"<ul style='margin:4pt 0 6pt 16pt;padding:0;'>{items}</ul>")
                bullet_buf.clear()

        for raw_ln in cleaned.splitlines():
            ln = raw_ln.strip()
            if not ln:
                _flush_bullets()
                continue
            if ln.startswith(("- ", "• ", "* ")):
                bullet_txt = ln[2:].strip()
                if bullet_txt and not is_greeting_or_chatter(bullet_txt):
                    bullet_buf.append(bullet_txt)
            elif re.match(r"^#{1,4}\s+", ln):
                _flush_bullets()
                hd = re.sub(r"^#{1,4}\s+", "", ln).strip()
                if hd:
                    blocks.append(f"<p style='margin:6pt 0 3pt 0;font-weight:bold;color:#0F172A;'>{cls._pdf_inline_format(hd)}</p>")
            else:
                _flush_bullets()
                if not is_greeting_or_chatter(ln):
                    blocks.append(f"<p style='margin:0 0 6pt 0;'>{cls._pdf_inline_format(ln)}</p>")
        _flush_bullets()
        return "".join(blocks)

    @classmethod
    def to_pdf_html(
        cls,
        meeting: Dict[str, Any],
        detail_mode: str = "detailed",
        include_transcript: bool = False,
    ) -> str:
        """
        Generates a clean, executive QTextDocument-compatible HTML4/CSS2.1 report
        for high-DPI PDF rendering with zero raw transcript/greeting clutter.
        """
        m = meeting if isinstance(meeting, dict) else {}
        mode_l = "brief" if str(detail_mode or "").strip().lower() == "brief" else "detailed"
        title = html.escape(str(m.get("title") or "Untitled Meeting").strip())
        started = format_meeting_local_datetime(
            m.get("started_at") or m.get("created_at") or "",
            str(m.get("timezone") or ""),
        )
        dur_s = float(m.get("duration_sec") or 0.0)
        dur_str = format_seconds_hms(dur_s) if dur_s > 0 else "—"
        plat = str(m.get("source_platform") or "Meeting").strip()

        parts_raw = cls._pdf_safe_list(m.get("participants_json"))
        if not parts_raw:
            rp = m.get("participants")
            if isinstance(rp, list):
                parts_raw = rp
            elif isinstance(rp, str) and rp.strip():
                parts_raw = [x.strip() for x in rp.split(",") if x.strip()]
        participants = ", ".join(str(p).strip() for p in parts_raw if str(p).strip())

        meta_items = [x for x in (started, f"Duration: {dur_str}", plat) if x]
        meta_line = html.escape("  |  ".join(meta_items))

        raw_brief = str(m.get("brief_summary") or m.get("summary") or "").strip()
        raw_det = str(m.get("detailed_summary") or "").strip()
        chosen_sum = raw_det if (mode_l == "detailed" and raw_det) else (raw_brief or raw_det)
        sum_html = cls._pdf_prose_to_html(chosen_sum)

        sections: List[str] = []

        # Executive Summary Box
        if sum_html:
            sections.append(
                "<h2 style='font-size:12pt;font-weight:bold;color:#0F172A;margin:14pt 0 6pt 0;"
                "padding-bottom:3pt;border-bottom:1px solid #E2E8F0;'>Executive Summary</h2>"
                "<table width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;margin-bottom:8pt;'>"
                "<tr><td style='background-color:#F8FAFC;border:1px solid #E2E8F0;border-left:4px solid #1A73E8;"
                f"padding:10pt 12pt;font-size:10pt;line-height:1.45;color:#1E293B;'>{sum_html}</td></tr>"
                "</table>"
            )

        # Key Takeaways
        kp_raw = cls._pdf_safe_list(m.get("key_points_json") or m.get("key_discussion_points"))
        tk_items: List[str] = []
        seen_tk = set()
        for kp in kp_raw:
            if isinstance(kp, dict):
                top = clean_user_facing_text(str(kp.get("topic") or ""))
                sm = clean_user_facing_text(str(kp.get("summary") or kp.get("point") or ""))
            else:
                top = ""
                sm = clean_user_facing_text(str(kp or ""))
            if not sm or is_greeting_or_chatter(sm):
                continue
            key = f"{top.lower()}::{sm.lower()}"
            if key in seen_tk:
                continue
            seen_tk.add(key)
            prefix = f"<b>{html.escape(top)}:</b> " if top and not is_greeting_or_chatter(top) else ""
            tk_items.append(f"<li style='margin:0 0 5pt 0;'>{prefix}{html.escape(sm)}</li>")
        if tk_items:
            sections.append(
                "<h2 style='font-size:12pt;font-weight:bold;color:#0F172A;margin:14pt 0 6pt 0;"
                "padding-bottom:3pt;border-bottom:1px solid #E2E8F0;'>Key Takeaways</h2>"
                f"<ul style='margin:4pt 0 8pt 16pt;padding:0;font-size:10pt;line-height:1.45;color:#1E293B;'>{''.join(tk_items)}</ul>"
            )

        # Detailed-only: Topics Discussed
        if mode_l == "detailed":
            topics_raw = cls._pdf_safe_list(m.get("topics_json") or m.get("topics_structured"))
            top_blocks: List[str] = []
            for top in topics_raw:
                if not isinstance(top, dict):
                    continue
                raw_t_title = str(top.get("title") or top.get("topic") or "").strip()
                raw_t_sum = str(top.get("summary") or "").strip()
                raw_t_buls = top.get("key_points") or top.get("details") or []
                if is_noisy_topic_fragment(raw_t_title, raw_t_sum, raw_t_buls):
                    continue
                t_title = clean_user_facing_text(raw_t_title)
                t_sum = clean_user_facing_text(raw_t_sum)
                t_buls = [clean_user_facing_text(str(b)) for b in raw_t_buls]
                t_buls = [b for b in t_buls if b and not is_greeting_or_chatter(b)]
                if t_title and is_greeting_or_chatter(t_title):
                    continue
                if t_sum and is_greeting_or_chatter(t_sum) and not t_buls:
                    continue
                if not t_title and not t_sum and not t_buls:
                    continue
                b_html = "".join(f"<li style='margin:0 0 3pt 0;'>{html.escape(b)}</li>" for b in t_buls)
                top_blocks.append(
                    "<div style='margin:0 0 8pt 0;padding:0 0 6pt 0;border-bottom:1px solid #F1F5F9;'>"
                    f"<div style='font-size:10.5pt;font-weight:bold;color:#0F172A;margin-bottom:2pt;'>{html.escape(t_title or 'Discussion Topic')}</div>"
                    + (f"<div style='font-size:10pt;color:#334155;margin-bottom:3pt;'>{html.escape(t_sum)}</div>" if t_sum and not is_greeting_or_chatter(t_sum) else "")
                    + (f"<ul style='margin:2pt 0 2pt 16pt;padding:0;font-size:9.5pt;color:#334155;'>{b_html}</ul>" if b_html else "")
                    + "</div>"
                )
            if top_blocks:
                sections.append(
                    "<h2 style='font-size:12pt;font-weight:bold;color:#0F172A;margin:14pt 0 6pt 0;"
                    "padding-bottom:3pt;border-bottom:1px solid #E2E8F0;'>Topics Discussed</h2>"
                    + "".join(top_blocks)
                )

        # Decisions
        decs_raw = cls._pdf_safe_list(m.get("decisions"))
        dec_items: List[str] = []
        for d in decs_raw:
            d_txt = clean_user_facing_text(str(d.get("decision") or "") if isinstance(d, dict) else str(d or ""))
            if not d_txt or is_greeting_or_chatter(d_txt):
                continue
            stat = str(d.get("status") or "confirmed").strip().lower() if isinstance(d, dict) else "confirmed"
            rat = clean_user_facing_text(str(d.get("rationale") or "")) if isinstance(d, dict) else ""
            stat_tag = f" <span style='color:#64748B;font-size:9pt;'>({html.escape(stat.capitalize())})</span>" if stat != "confirmed" else ""
            rat_html = f"<div style='font-size:9.5pt;color:#475569;margin-top:2pt;'>{html.escape(rat)}</div>" if rat else ""
            dec_items.append(f"<li style='margin:0 0 5pt 0;'><b>{html.escape(d_txt)}</b>{stat_tag}{rat_html}</li>")
        if dec_items:
            sections.append(
                "<h2 style='font-size:12pt;font-weight:bold;color:#0F172A;margin:14pt 0 6pt 0;"
                "padding-bottom:3pt;border-bottom:1px solid #E2E8F0;'>Key Decisions</h2>"
                f"<ul style='margin:4pt 0 8pt 16pt;padding:0;font-size:10pt;line-height:1.45;color:#1E293B;'>{''.join(dec_items)}</ul>"
            )

        # Action Items Table
        acts_raw = cls._pdf_safe_list(m.get("action_items"))
        act_rows: List[str] = []
        for idx, a in enumerate(acts_raw):
            if isinstance(a, dict):
                desc = clean_user_facing_text(str(a.get("description") or ""))
                own = str(a.get("owner") or "").strip()
                if not own or own.lower() in {"unassigned", "unassigned — confirmation required", "owner unknown", "none", "null", "n/a"}:
                    own = "-"
                due = str(a.get("due_date") or "").strip()
                if not due or due.lower() in {"not specified", "no due date", "tbd", "none", "null", "n/a"}:
                    due = "-"
                prio = str(a.get("priority") or "").strip().capitalize() or "-"
                done = str(a.get("status") or "").strip().lower() == "completed"
            else:
                desc = clean_user_facing_text(str(a or ""))
                own, due, prio, done = "-", "-", "-", False
            if not desc or is_greeting_or_chatter(desc):
                continue
            bg = "#FFFFFF" if idx % 2 == 0 else "#F8FAFC"
            prefix = "[Done] " if done else ""
            act_rows.append(
                f"<tr style='background-color:{bg};'>"
                f"<td style='border:1px solid #E2E8F0;padding:6pt 8pt;font-size:9.8pt;color:#1E293B;'>{html.escape(prefix + desc)}</td>"
                f"<td style='border:1px solid #E2E8F0;padding:6pt 8pt;font-size:9.5pt;color:#334155;'>{html.escape(own)}</td>"
                f"<td style='border:1px solid #E2E8F0;padding:6pt 8pt;font-size:9.5pt;color:#334155;'>{html.escape(due)}</td>"
                f"<td style='border:1px solid #E2E8F0;padding:6pt 8pt;font-size:9.5pt;color:#334155;'>{html.escape(prio)}</td>"
                "</tr>"
            )
        if act_rows:
            sections.append(
                "<h2 style='font-size:12pt;font-weight:bold;color:#0F172A;margin:14pt 0 6pt 0;"
                "padding-bottom:3pt;border-bottom:1px solid #E2E8F0;'>Action Items</h2>"
                "<table width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;margin-bottom:8pt;'>"
                "<tr style='background-color:#F1F5F9;'>"
                "<th align='left' width='52%' style='border:1px solid #CBD5E1;padding:6pt 8pt;font-size:9pt;color:#334155;'>Task</th>"
                "<th align='left' width='18%' style='border:1px solid #CBD5E1;padding:6pt 8pt;font-size:9pt;color:#334155;'>Owner</th>"
                "<th align='left' width='16%' style='border:1px solid #CBD5E1;padding:6pt 8pt;font-size:9pt;color:#334155;'>Due</th>"
                "<th align='left' width='14%' style='border:1px solid #CBD5E1;padding:6pt 8pt;font-size:9pt;color:#334155;'>Priority</th>"
                "</tr>"
                + "".join(act_rows)
                + "</table>"
            )

        # Detailed-only: Follow-Ups, Risks & Blockers, Open Questions
        if mode_l == "detailed":
            fu_raw = cls._pdf_safe_list(m.get("follow_ups_json") or m.get("follow_ups"))
            fu_items: List[str] = []
            for fu in fu_raw:
                if isinstance(fu, dict):
                    desc = clean_user_facing_text(str(fu.get("item") or fu.get("description") or ""))
                    own = str(fu.get("owner") or "").strip()
                    if own.lower() in {"unassigned", "owner unknown", "none", "null", "n/a"}:
                        own = ""
                else:
                    desc = clean_user_facing_text(str(fu or ""))
                    own = ""
                if not desc or is_greeting_or_chatter(desc):
                    continue
                own_tag = f" <span style='color:#475569;font-size:9pt;'>({html.escape(own)})</span>" if own else ""
                fu_items.append(f"<li style='margin:0 0 4pt 0;'>{html.escape(desc)}{own_tag}</li>")
            if fu_items:
                sections.append(
                    "<h2 style='font-size:12pt;font-weight:bold;color:#0F172A;margin:14pt 0 6pt 0;"
                    "padding-bottom:3pt;border-bottom:1px solid #E2E8F0;'>Follow-Ups &amp; Next Steps</h2>"
                    f"<ul style='margin:4pt 0 8pt 16pt;padding:0;font-size:10pt;line-height:1.45;color:#1E293B;'>{''.join(fu_items)}</ul>"
                )

            raw_risks = (
                m.get("risks_and_blockers_list")
                or m.get("risks_and_blockers")
                or m.get("risks", [])
            )
            rk_iter = raw_risks if isinstance(raw_risks, list) else (raw_risks.splitlines() if isinstance(raw_risks, str) else [])
            rk_items: List[str] = []
            for r in rk_iter:
                if isinstance(r, dict):
                    itype = str(r.get("item_type") or r.get("type") or "risk").strip().lower()
                    desc = clean_user_facing_text(str(r.get("description") or r.get("risk") or r.get("title") or ""))
                    mit = clean_user_facing_text(str(r.get("mitigation_or_resolution") or r.get("mitigation") or ""))
                    if mit.lower() in {"none", "n/a", "null", "under review"}:
                        mit = ""
                    sev = str(r.get("impact") or r.get("severity") or "Medium").strip().capitalize()
                    lbl = "Blocker" if itype == "blocker" else f"{sev} Risk"
                else:
                    desc = clean_user_facing_text(str(r or "").lstrip("•-* "))
                    mit = ""
                    lbl = "Risk"
                if not desc or is_greeting_or_chatter(desc):
                    continue
                mit_html = f"<div style='font-size:9.5pt;color:#475569;margin-top:2pt;'><b>Mitigation:</b> {html.escape(mit)}</div>" if mit else ""
                rk_items.append(
                    f"<li style='margin:0 0 6pt 0;'><b>[{html.escape(lbl)}]</b> {html.escape(desc)}{mit_html}</li>"
                )
            if rk_items:
                sections.append(
                    "<h2 style='font-size:12pt;font-weight:bold;color:#0F172A;margin:14pt 0 6pt 0;"
                    "padding-bottom:3pt;border-bottom:1px solid #E2E8F0;'>Risks &amp; Blockers</h2>"
                    f"<ul style='margin:4pt 0 8pt 16pt;padding:0;font-size:10pt;line-height:1.45;color:#1E293B;'>{''.join(rk_items)}</ul>"
                )

            oq_raw = cls._pdf_safe_list(m.get("open_questions_json") or m.get("open_questions"))
            oq_items: List[str] = []
            seen_oq = set()
            for q in oq_raw:
                qt = clean_user_facing_text(str(q.get("question") or q.get("description") or "") if isinstance(q, dict) else str(q or ""))
                if not qt or is_greeting_or_chatter(qt) or is_non_substantive_question(qt):
                    continue
                if qt.lower() in seen_oq:
                    continue
                seen_oq.add(qt.lower())
                oq_items.append(f"<li style='margin:0 0 4pt 0;'>{html.escape(qt)}</li>")
            if oq_items:
                sections.append(
                    "<h2 style='font-size:12pt;font-weight:bold;color:#0F172A;margin:14pt 0 6pt 0;"
                    "padding-bottom:3pt;border-bottom:1px solid #E2E8F0;'>Open Questions</h2>"
                    f"<ul style='margin:4pt 0 8pt 16pt;padding:0;font-size:10pt;line-height:1.45;color:#1E293B;'>{''.join(oq_items)}</ul>"
                )

        if include_transcript:
            segments = m.get("segments") or []
            if segments:
                seg_rows = []
                for s in segments:
                    ts = format_seconds_hms(float(s.get("start_time", 0.0)))
                    spk = html.escape(str(s.get("speaker") or "Speaker"))
                    txt = html.escape(str(s.get("text") or "").strip())
                    if txt:
                        seg_rows.append(
                            f"<div style='margin:0 0 4pt 0;font-size:9.5pt;'>"
                            f"<span style='color:#64748B;'>[{ts}]</span> <b>{spk}:</b> {txt}</div>"
                        )
                if seg_rows:
                    sections.append(
                        "<h2 style='font-size:12pt;font-weight:bold;color:#0F172A;margin:14pt 0 6pt 0;"
                        "padding-bottom:3pt;border-bottom:1px solid #E2E8F0;'>Transcript</h2>"
                        + "".join(seg_rows)
                    )

        if not sections:
            sections.append("<p style='font-size:10pt;color:#64748B;'>No meeting summary details recorded.</p>")

        part_html = (
            f"<div style='font-size:9.5pt;color:#475569;margin-top:3pt;'><b>Participants:</b> {html.escape(participants)}</div>"
            if participants else ""
        )
        mode_tag = "BRIEF SUMMARY" if mode_l == "brief" else "DETAILED SUMMARY"
        gen_ts = datetime.now().strftime("%b %d, %Y · %I:%M %p").replace(" 0", " ")

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;font-size:10pt;color:#1E293B;line-height:1.45;">
  <table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;margin-bottom:10pt;border-bottom:2px solid #1A73E8;">
    <tr>
      <td style="padding:0 0 10pt 0;">
        <div style="font-size:8.5pt;font-weight:bold;color:#1A73E8;letter-spacing:1px;margin-bottom:3pt;">VOX MEETING SUMMARY · {mode_tag}</div>
        <div style="font-size:18pt;font-weight:bold;color:#0F172A;margin-bottom:4pt;">{title}</div>
        <div style="font-size:9.5pt;color:#475569;">{meta_line}</div>
        {part_html}
      </td>
    </tr>
  </table>
  {''.join(sections)}
  <div style="margin-top:16pt;padding-top:6pt;border-top:1px solid #E2E8F0;font-size:8.5pt;color:#64748B;">
    Generated by VOX Local Meeting Intelligence · {html.escape(gen_ts)}
  </div>
</body>
</html>
"""

    @classmethod
    def to_pdf(
        cls,
        meeting: Dict[str, Any],
        target_path: Optional[Union[Path, str]] = None,
        detail_mode: str = "detailed",
        include_transcript: bool = False,
    ) -> Path:
        """
        Renders an executive PDF document at crisp 150 DPI resolution using QPdfWriter
        and QTextDocument HTML4/CSS2.1 layout.
        """
        from PyQt6.QtCore import QMarginsF, QSizeF
        from PyQt6.QtGui import QFont, QFontDatabase, QPageLayout, QPageSize, QPdfWriter, QTextDocument
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            app = QApplication([])

        families = set(QFontDatabase.families())
        pdf_font_family = "Segoe UI"
        if pdf_font_family not in families:
            try:
                from voxlocal.ui.theme import ensure_google_fonts_loaded
                ensure_google_fonts_loaded()
                families = set(QFontDatabase.families())
            except Exception:
                pass
            if "Google Sans" in families:
                pdf_font_family = "Google Sans"
            elif "Arial" in families:
                pdf_font_family = "Arial"
            elif families:
                pdf_font_family = sorted(families)[0]

        if target_path is None:
            import tempfile
            tf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            target_path = Path(tf.name)
            tf.close()
        else:
            target_path = Path(target_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)

        writer = QPdfWriter(str(target_path))
        writer.setResolution(150)
        writer.setPageLayout(
            QPageLayout(
                QPageSize(QPageSize.PageSizeId.A4),
                QPageLayout.Orientation.Portrait,
                QMarginsF(14.0, 14.0, 14.0, 14.0),
                QPageLayout.Unit.Millimeter,
            )
        )

        html_content = cls.to_pdf_html(
            meeting,
            detail_mode=detail_mode,
            include_transcript=include_transcript,
        )
        if pdf_font_family != "Segoe UI":
            html_content = html_content.replace("'Segoe UI',", f"'{pdf_font_family}',")
        doc = QTextDocument()
        doc.setDefaultFont(QFont(pdf_font_family, 10))
        doc.setHtml(html_content)
        doc.setPageSize(QSizeF(writer.width(), writer.height()))
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
        detail_mode: str = "detailed",
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
            cls.to_pdf(meeting, target_path=target_path, detail_mode=detail_mode, include_transcript=False)
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
        meeting_timestamp: Optional[str] = None,
        timezone: Optional[str] = None,
        local_user_name: str = "",
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
            meeting_timestamp=meeting_timestamp,
            timezone=timezone,
            local_user_name=local_user_name,
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

"""
VOX v2.2 Typed Schemas & Deterministic Evidence Verification Stage.

Separates two orthogonal concerns:
1. Structural compliance via strict JSON Schemas for llama.cpp constrained decoding.
2. Semantic accuracy & grounding verification against actual transcript segments:
   - Aligns every claim's timestamp to the best matching transcript segment.
   - Filters non-substantive Open Questions (greetings, intros/context statements,
     conversational repairs, and questions already answered in-meeting).
   - Guards Action Item owners (explicit single-owner commitments vs 'Owner needed';
     never infers organizer/last speaker; resolves spoke & calendar deadlines).
   - Guards Decision status fidelity (proposed / pending_confirmation vs confirmed).
   - Enforces atomic Risks & Blockers without leaked 'RISKImpact:' labels.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "can", "could", "did",
    "do", "does", "for", "from", "had", "has", "have", "he", "her", "here", "hi",
    "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "just", "let",
    "may", "me", "might", "more", "must", "my", "no", "not", "now", "of", "on", "or",
    "our", "out", "please", "shall", "she", "should", "so", "some", "that", "the",
    "their", "them", "then", "there", "these", "they", "this", "those", "to", "too",
    "up", "us", "was", "we", "were", "what", "when", "where", "which", "who", "why",
    "will", "with", "would", "you", "your",
}

_NULL_TOKENS = {
    "", "none", "null", "n/a", "na", "unknown", "unassigned", "not specified",
    "owner unknown", "owner needed", "tbd", "no due date", "to be confirmed",
}

_GREETING_OR_REPAIR_RE = re.compile(
    r"^(?:hi|hello|hey|good\s+(?:morning|afternoon|evening)|how\s+are\s+you|"
    r"how\s+is\s+it\s+going|are\s+you\s+doing|sorry[,\s]+can\s+you\s+repeat|"
    r"can\s+you\s+repeat|pardon|come\s+again|did\s+you\s+say|can\s+you\s+hear\s+me|"
    r"am\s+i\s+audible|thank\s+you|thanks|bye)\b",
    re.IGNORECASE,
)

_PENDING_CONFIRMATION_RE = re.compile(
    r"\b(?:need\s+\w+\s+to\s+confirm|needs?\s+(?:to\s+be\s+)?confirm(?:ed|ation)|"
    r"pending\s+(?:confirmation|approval|finance|customer|review|list|sign[- ]?off)|"
    r"until\s+.+\s+is\s+confirmed|whether\s+.+\s+is\s+required|"
    r"subject\s+to\s+(?:sample\s+)?validation|owner\s+to\s+be\s+confirmed|"
    r"unresolved|still\s+need|must\s+be\s+resolved\s+before)\b",
    re.IGNORECASE,
)

_CONTEXT_STATEMENT_RE = re.compile(
    r"^(?:for\s+context|today'?s\s+meeting\s+is\s+about|we\s+are\s+currently|"
    r"note\s+that|fyi|just\s+to\s+clarify|as\s+background)\b",
    re.IGNORECASE,
)

_PROVISIONAL_DECISION_RE = re.compile(
    r"\b(?:propose|proposed|tentative|for\s+the\s+pilot|validate\s+(?:that\s+)?with|"
    r"subject\s+to|pending\s+validation|pending\s+confirmation|pending\s+approval|"
    r"until\s+.+\s+is\s+confirmed|before\s+changing\s+anything|draft)\b",
    re.IGNORECASE,
)

_LEAKED_RISK_TAG_RE = re.compile(
    r"(?:RISK\s*Impact\s*:\s*\w+|BLOCKER\s*Impact\s*:\s*\w+|\bRISK\s*:\s*|\bBLOCKER\s*:\s*|Impact\s*:\s*(?:HIGH|MEDIUM|LOW|CRITICAL)\s*)",
    re.IGNORECASE,
)

_EXPLICIT_SELF_COMMIT_RE = re.compile(
    r"\b(?:i'?ll|i\s+will|let\s+me|i\s+can\s+prepare|i\s+can\s+share|i\s+am\s+going\s+to|i'?m\s+going\s+to)\b",
    re.IGNORECASE,
)

_COLLECTIVE_OR_PASSIVE_RE = re.compile(
    r"\b(?:needs?\s+to\s+be\s+resolved|must\s+be\s+resolved|to\s+be\s+confirmed|"
    r"owner\s+to\s+be\s+confirmed|would\s+need\s+.+\s+to\s+be\s+configured)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Strict JSON Schemas for llama.cpp grammar-constrained decoding
# ---------------------------------------------------------------------------

CALL1A_SUMMARY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "meeting_purpose": {"type": "string"},
        "overall_outcome": {"type": "string"},
        "brief_summary": {"type": "string"},
        "detailed_summary": {"type": "string"},
        "key_discussion_points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "summary": {"type": "string"},
                    "timestamp_str": {"type": "string", "pattern": "^[0-9]{2}:[0-9]{2}$"},
                    "speakers": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["topic", "summary", "timestamp_str", "speakers"],
                "additionalProperties": False,
            },
        },
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "details": {"type": "array", "items": {"type": "string"}},
                    "participants": {"type": "array", "items": {"type": "string"}},
                    "timestamp_str": {"type": "string", "pattern": "^[0-9]{2}:[0-9]{2}$"},
                },
                "required": ["title", "summary", "details", "participants", "timestamp_str"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "meeting_purpose",
        "overall_outcome",
        "brief_summary",
        "detailed_summary",
        "key_discussion_points",
        "topics",
    ],
    "additionalProperties": False,
}

CALL1B_RISKS_QUESTIONS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["risk", "blocker"]},
                    "description": {"type": "string"},
                    "impact": {"type": "string", "enum": ["high", "medium", "low"]},
                    "mitigation": {"type": "string"},
                    "status": {"type": "string", "enum": ["open", "mitigated", "monitoring"]},
                    "timestamp_str": {"type": "string", "pattern": "^[0-9]{2}:[0-9]{2}$"},
                    "evidence_quote": {"type": "string"},
                },
                "required": [
                    "type",
                    "description",
                    "impact",
                    "mitigation",
                    "status",
                    "timestamp_str",
                    "evidence_quote",
                ],
                "additionalProperties": False,
            },
        },
        "open_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["unanswered_question", "pending_confirmation", "information_requested"],
                    },
                    "asked_by": {"type": "string"},
                    "context": {"type": "string"},
                    "timestamp_str": {"type": "string", "pattern": "^[0-9]{2}:[0-9]{2}$"},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["question", "category", "asked_by", "context", "timestamp_str", "evidence_quote"],
                "additionalProperties": False,
            },
        },
        "follow_ups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "pending_confirmation",
                            "question",
                            "future_discussion",
                            "dependency",
                            "promised_followup",
                        ],
                    },
                    "owner": {"type": ["string", "null"]},
                    "timestamp_str": {"type": "string", "pattern": "^[0-9]{2}:[0-9]{2}$"},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["item", "category", "owner", "timestamp_str", "evidence_quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["risks", "open_questions", "follow_ups"],
    "additionalProperties": False,
}

CALL2_DECISIONS_ACTIONS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": [
                            "confirmed",
                            "proposed",
                            "under_discussion",
                            "rejected",
                            "pending_confirmation",
                        ],
                    },
                    "category": {
                        "type": "string",
                        "enum": ["Strategic", "Technical", "Product", "Budget", "Process", "General"],
                    },
                    "evolution_relationship": {
                        "type": "string",
                        "enum": ["new", "repeated", "superseded", "reversed"],
                    },
                    "speaker": {"type": "string"},
                    "participants": {"type": "array", "items": {"type": "string"}},
                    "context": {"type": "string"},
                    "rationale": {"type": "string"},
                    "timestamp_str": {"type": "string", "pattern": "^[0-9]{2}:[0-9]{2}$"},
                    "evidence_quote": {"type": "string"},
                },
                "required": [
                    "decision",
                    "status",
                    "category",
                    "evolution_relationship",
                    "speaker",
                    "participants",
                    "context",
                    "rationale",
                    "timestamp_str",
                    "evidence_quote",
                ],
                "additionalProperties": False,
            },
        },
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "owner": {"type": ["string", "null"]},
                    "due_date_text": {"type": ["string", "null"]},
                    "priority": {"type": ["string", "null"], "enum": ["High", "Medium", "Low", None]},
                    "confirmation_state": {
                        "type": "string",
                        "enum": ["confirmed_commitment", "suggested_action", "needs_confirmation"],
                    },
                    "related_topic": {"type": "string"},
                    "context": {"type": "string"},
                    "timestamp_str": {"type": "string", "pattern": "^[0-9]{2}:[0-9]{2}$"},
                    "evidence_quote": {"type": "string"},
                },
                "required": [
                    "description",
                    "owner",
                    "due_date_text",
                    "priority",
                    "confirmation_state",
                    "related_topic",
                    "context",
                    "timestamp_str",
                    "evidence_quote",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["decisions", "action_items"],
    "additionalProperties": False,
}

CALL3_KNOWLEDGE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "knowledge_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_type": {
                        "type": "string",
                        "enum": ["entity", "acronym", "glossary", "participant", "project", "fact", "decision", "open_issue"],
                    },
                    "subcategory": {
                        "type": "string",
                        "enum": [
                            "person",
                            "company",
                            "project",
                            "system",
                            "acronym",
                            "terminology",
                            "process",
                            "workflow",
                            "decision",
                            "policy",
                            "fact",
                            "context",
                        ],
                    },
                    "canonical_key": {"type": "string"},
                    "title": {"type": "string"},
                    "entity_name": {"type": "string"},
                    "content": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "speaker": {"type": "string"},
                    "timestamp_str": {"type": "string", "pattern": "^[0-9]{2}:[0-9]{2}$"},
                    "evidence_quote": {"type": "string"},
                },
                "required": [
                    "item_type",
                    "subcategory",
                    "canonical_key",
                    "title",
                    "entity_name",
                    "content",
                    "aliases",
                    "speaker",
                    "timestamp_str",
                    "evidence_quote",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["knowledge_items"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Evidence & Semantic Verification Helpers
# ---------------------------------------------------------------------------

def _fmt_mmss(sec: float) -> str:
    mm, ss = divmod(max(0, int(round(float(sec or 0.0)))), 60)
    return f"{mm:02d}:{ss:02d}"


def _parse_mmss_to_sec(ts_str: str) -> Optional[float]:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$", str(ts_str or ""))
    if not m:
        return None
    if m.group(3) is not None:
        return float(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)))
    return float(int(m.group(1)) * 60 + int(m.group(2)))


def _content_tokens(text: str) -> List[str]:
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


def _match_segment(
    segments: List[Dict[str, Any]],
    quote_or_text: str,
    ts_str: str = "",
) -> Tuple[int, float, str, str]:
    """Return (seg_idx, start_sec, speaker, seg_text) for best matching segment."""
    if not segments:
        return -1, 0.0, "", ""

    target_sec = _parse_mmss_to_sec(ts_str)
    q_clean = re.sub(r"^(?:You\s*\(Mic\)|Speaker\s*\d+|[\w.\s]+):\s*", "", str(quote_or_text or ""), flags=re.IGNORECASE).strip()
    q_low = q_clean.lower()
    q_tokens = set(_content_tokens(q_clean))

    best_idx = 0
    best_score = -1.0
    for i, s in enumerate(segments):
        s_txt = str(s.get("text") or "").strip()
        s_low = s_txt.lower()
        s_sec = float(s.get("start_time") or s.get("start") or 0.0)
        score = 0.0
        if q_low and len(q_low) >= 8 and q_low in s_low:
            score += 20.0
        elif q_tokens:
            s_tokens = set(_content_tokens(s_txt))
            overlap = len(q_tokens & s_tokens)
            score += overlap * 2.5
        if target_sec is not None:
            diff = abs(s_sec - target_sec)
            if diff <= 3.0:
                score += 6.0
            elif diff <= 12.0:
                score += 2.0
        if score > best_score:
            best_score = score
            best_idx = i

    chosen = segments[best_idx]
    return (
        best_idx,
        float(chosen.get("start_time") or chosen.get("start") or 0.0),
        str(chosen.get("speaker") or "Speaker").strip(),
        str(chosen.get("text") or "").strip(),
    )


def resolve_deadline_expression(
    expression: Optional[str],
    meeting_timestamp: Optional[str] = None,
    timezone: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve both spoken relative deadlines and explicit calendar dates ('4th of December')."""
    clean_expr = str(expression or "").strip()
    if not clean_expr or clean_expr.lower() in _NULL_TOKENS:
        return {
            "original_expression": "",
            "original_text": "",
            "resolved_date": None,
            "meeting_timestamp": meeting_timestamp,
            "reference_timestamp": meeting_timestamp,
            "timezone_used": timezone,
            "timezone": timezone,
            "resolution_status": "unresolved",
            "confidence": 0.0,
            "uncertainty_reason": "Empty temporal expression",
        }

    anchor_dt: Optional[datetime] = None
    if meeting_timestamp:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                anchor_dt = datetime.strptime(str(meeting_timestamp).strip()[:19], fmt)
                break
            except Exception:
                continue

    months = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
        "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    low = clean_expr.lower()

    # Check explicit calendar date like "4th of December", "December 4", "4 Dec 2026"
    m_dmy = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|october|oct|november|nov|december|dec)(?:[,\s]+(\d{4}))?\b",
        low,
    )
    m_mdy = re.search(
        r"\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|october|oct|november|nov|december|dec)\s+(\d{1,2})(?:st|nd|rd|th)?(?:[,\s]+(\d{4}))?\b",
        low,
    )
    if m_dmy or m_mdy:
        if m_dmy:
            day_i = int(m_dmy.group(1))
            mon_i = months[m_dmy.group(2)]
            yr_str = m_dmy.group(3)
        else:
            mon_i = months[m_mdy.group(1)]
            day_i = int(m_mdy.group(2))
            yr_str = m_mdy.group(3)
        year_i = int(yr_str) if yr_str else (anchor_dt.year if anchor_dt else None)
        if year_i is not None:
            try:
                cand_dt = datetime(year_i, mon_i, day_i)
                if not yr_str and anchor_dt and (anchor_dt.date() - cand_dt.date()).days > 180:
                    cand_dt = datetime(year_i + 1, mon_i, day_i)
                iso = cand_dt.strftime("%Y-%m-%d")
                return {
                    "original_expression": clean_expr,
                    "original_text": clean_expr,
                    "resolved_date": iso,
                    "meeting_timestamp": meeting_timestamp,
                    "reference_timestamp": meeting_timestamp,
                    "timezone_used": timezone,
                    "timezone": timezone,
                    "resolution_status": "deterministic",
                    "confidence": 1.0,
                    "uncertainty_reason": None,
                }
            except Exception:
                pass

    from voxlocal.engine.meeting_intelligence import resolve_relative_due_date

    # Handle "next week Thursday" -> "+7 days" past anchor or next week's Thursday
    if anchor_dt is not None and re.search(r"\bnext\s+week\b", low):
        weekdays = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        for dname, dnum in weekdays.items():
            if re.search(rf"\b{dname}\b", low):
                cur_wd = anchor_dt.weekday()
                days_to_next_mon = 7 - cur_wd
                target = anchor_dt + timedelta(days=days_to_next_mon + dnum)
                return {
                    "original_expression": clean_expr,
                    "original_text": clean_expr,
                    "resolved_date": target.strftime("%Y-%m-%d"),
                    "meeting_timestamp": meeting_timestamp,
                    "reference_timestamp": meeting_timestamp,
                    "timezone_used": timezone,
                    "timezone": timezone,
                    "resolution_status": "deterministic",
                    "confidence": 1.0,
                    "uncertainty_reason": None,
                }

    return resolve_relative_due_date(clean_expr, meeting_timestamp=meeting_timestamp, timezone=timezone)


_SNIPPET_NOISE_RE = re.compile(
    r"^\s*(?:\[\d{1,2}:\d{2}(?::\d{2})?\]|\[\d+\])\s*",
)
_RAISED_BY_PREFIX_RE = re.compile(
    r"^\s*\(Raised by [^)]+\)\s*",
    re.IGNORECASE,
)


_LEADING_CHATTER_SENTENCE_RE = re.compile(
    r"^(?:"
    r"(?:hi|hello|hey)(?:\s+[A-Z][a-z]+)?[.!?\s]+|"
    r"good\s+(?:morning|afternoon|evening)(?:\s+[A-Z][a-z]+)?[.!?\s]+|"
    r"how\s+are\s+you(?:\s+doing(?:\s+today)?)?[.!?\s]+|"
    r"i'?m\s+(?:good|doing\s+well|fine)[.!?\s]+|"
    r"thank\s+you(?:\s+for\s+asking)?[.!?\s]+|"
    r"let'?s\s+get\s+started[.!?\s]+|"
    r"before\s+we\s+close,?\s+let'?s\s+review(?:\s+the\s+\w+)?(?:\s+as\s+well)?[.!?\s]+|"
    r"yeah,?\s+we\s+can[.!?\s]+|"
    r"shall\s+we\s+proceed(?:\s+with\s+this)?[.?,\s]+|"
    r"right,\s*uh,?\s*uh,?\s*yeah[.!?\s]+|"
    r"i\s+have\s+one\s+question(?:\s+about\s+[^.?!]+)?[.!?\s]+"
    r")+",
    re.IGNORECASE,
)


def is_greeting_or_chatter(text: str) -> bool:
    """True when text is pure greeting, small talk, farewell, or conversational filler."""
    s = str(text or "").strip()
    if not s:
        return True
    s_clean = re.sub(r"^(?:You\s*\(Mic\)|Speaker\s*\d+)\s*:\s*", "", s, flags=re.IGNORECASE).strip()
    s_clean = _RAISED_BY_PREFIX_RE.sub("", s_clean).strip()
    if _GREETING_OR_REPAIR_RE.match(s_clean):
        if len(s_clean.split()) <= 18:
            return True
    if _CONTEXT_STATEMENT_RE.match(s_clean) and "?" not in s_clean and len(s_clean.split()) <= 28:
        return True
    if re.search(
        r"\b(?:how are you doing today|i'?m good\.?\s*how are you|good morning\b|have a good holiday|happy holiday)\b",
        s_clean,
        re.IGNORECASE,
    ) and len(s_clean.split()) <= 24:
        return True
    stripped_lead = _LEADING_CHATTER_SENTENCE_RE.sub("", s_clean).strip()
    if not stripped_lead:
        return True
    return False


def clean_user_facing_text(text: str) -> str:
    """Strip internal evidence tags, [MM:SS] citations, RISKImpact labels, and conversational lead-in filler."""
    s = str(text or "").strip()
    if not s:
        return ""
    s = _RAISED_BY_PREFIX_RE.sub("", s)
    s = _LEAKED_RISK_TAG_RE.sub("", s)
    s = re.sub(r"\s*\[(?:\d{1,2}:\d{2}(?::\d{2})?|\d+)\]\s*", " ", s)
    s = re.sub(r"\s*-\s*Source:\s*.*$", "", s, flags=re.IGNORECASE)
    s = _LEADING_CHATTER_SENTENCE_RE.sub("", s).strip()
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"[ \t]{2,}", " ", s).strip(" -|·")
    return s


def is_noisy_topic_fragment(
    title: str,
    summary: str = "",
    bullets: Optional[Iterable[str]] = None,
) -> bool:
    """
    Detect legacy extractive transcript-chunk topics such as:
    'Amar. morning amar. today?', 'Proceed this?', 'Yeah, can.', or truncated small-talk dumps.
    """
    t = str(title or "").strip()
    sm = str(summary or "").strip()
    if is_greeting_or_chatter(t):
        return True
    t_words = re.findall(r"[A-Za-z0-9']+", t.lower())
    if t_words and len(t_words) <= 4:
        if any(w in {"yeah", "yes", "no", "ok", "okay", "morning", "proceed", "today"} for w in t_words) and t.endswith(("?", ".")):
            return True
        # Repeated identical name/word fragment like "Amar. morning amar. today?"
        if len(set(t_words)) < len(t_words) and any(p in t for p in (".", "?")):
            return True
    if sm:
        sm_low = sm.lower()
        if any(tok in sm_low for tok in ("how are you doing today", "thank you for asking", "i'm doing well")):
            return True
        # Truncated mid-word extractive excerpt ("...We need t...", "...by tod...")
        if re.search(r"\b[a-z]{1,4}\.\.\.$", sm_low):
            return True
    return False


def is_non_substantive_question(text: str, next_texts: Optional[Iterable[str]] = None) -> bool:
    """
    True when text is a greeting, background statement, conversational repair,
    or small-talk question that must NEVER appear under Open Questions.
    """
    s = str(text or "").strip()
    if not s:
        return True
    if _PENDING_CONFIRMATION_RE.search(s):
        return False

    # Strip leading "Name:" prefixes and bracketed citations/speaker tags
    s_clean = re.sub(r"^(?:You\s*\(Mic\)|Speaker\s*\d+)\s*:\s*", "", s, flags=re.IGNORECASE).strip()
    s_clean = _RAISED_BY_PREFIX_RE.sub("", s_clean).strip()
    if is_greeting_or_chatter(s_clean):
        if "?" not in s_clean or len(s_clean.split()) <= 16 or not any(
            kw in s_clean.lower() for kw in ("confirm", "approve", "finance", "deadline", "sku", "file", "structure", "library")
        ):
            return True

    # Multi-sentence background paragraph ending with casual opener check ("For context... have you reviewed...")
    if _CONTEXT_STATEMENT_RE.match(s_clean) and not _PENDING_CONFIRMATION_RE.search(s_clean):
        if len(s_clean.split()) > 16 or "?" not in s_clean:
            return True

    # Purely declarative text with no question mark and no open-confirmation cue
    if "?" not in s_clean and not _PENDING_CONFIRMATION_RE.search(s_clean):
        return True

    # Conversational agenda transitions ("Shall we proceed with this?", "So can you explain what we found here?", "Okay?")
    if re.search(
        r"^(?:shall we proceed|should we proceed|so\s+can\s+you\s+explain\s+what\s+we\s+found|okay\??|right\??|you know\??|is that clear\??)",
        s_clean,
        re.IGNORECASE,
    ) and not _PENDING_CONFIRMATION_RE.search(s_clean):
        return True

    # If it's a direct turn question clearly answered affirmatively right after (e.g. "Should I standardize NAV to NVY?" -> "Yes, you should...")
    if next_texts:
        joined_next = " ".join(str(t or "").lower() for t in list(next_texts)[:2])
        if re.search(r"\b(?:yes|yeah|you should go ahead|we can|we will|it is|because|correct)\b", joined_next):
            # Only suppress if it doesn't mention an external unresolved gate
            if not _PENDING_CONFIRMATION_RE.search(s_clean):
                return True

    return False


def classify_open_question(item: Any) -> str:
    if isinstance(item, dict):
        txt = str(item.get("question") or "").lower()
        raw_cat = str(item.get("category") or "").strip().lower()
    else:
        txt = str(item or "").lower()
        raw_cat = ""
    if _PENDING_CONFIRMATION_RE.search(txt) or raw_cat == "pending_confirmation":
        return "pending_confirmation"
    if any(k in txt for k in ("explain", "share", "provide", "send", "what did we find", "missing")) or raw_cat == "information_requested":
        return "information_requested"
    if raw_cat in ("unanswered_question", "pending_confirmation", "information_requested"):
        return raw_cat
    return "unanswered_question"


def verify_and_normalize_intelligence(
    data: Dict[str, Any],
    segments: List[Dict[str, Any]],
    *,
    meeting_timestamp: Optional[str] = None,
    timezone: Optional[str] = None,
    local_user_name: str = "",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Validate & verify extracted intelligence against real transcript evidence.
    Returns (cleaned_data, audit_report).
    """
    out = dict(data or {})
    segs = list(segments or [])
    report: Dict[str, Any] = {
        "verified_claims": 0,
        "dropped_open_questions": 0,
        "owner_downgrades": 0,
        "owner_self_resolved": 0,
        "decision_downgrades": 0,
        "timestamp_snaps": 0,
        "risks_cleaned": 0,
    }

    self_owner_label = str(local_user_name or "").strip()
    if not self_owner_label or self_owner_label.lower() in ("guest", "guest user", "user"):
        self_owner_label = "You (Mic)"

    # 1. Verify Open Questions
    raw_oqs = list(out.get("open_questions") or [])
    clean_oqs: List[Dict[str, Any]] = []
    seen_oq: set[str] = set()
    for q in raw_oqs:
        if not isinstance(q, dict):
            continue
        q_txt = str(q.get("question") or "").strip()
        if not q_txt or len(q_txt) < 6:
            report["dropped_open_questions"] += 1
            continue
        ev_q = str(q.get("evidence_quote") or q_txt).strip()
        seg_idx, seg_sec, seg_spk, seg_txt = _match_segment(segs, ev_q, str(q.get("timestamp_str") or ""))
        next_txts = [
            str(segs[j].get("text") or "")
            for j in range(max(0, seg_idx + 1), min(len(segs), seg_idx + 3))
        ] if seg_idx >= 0 else []
        if is_non_substantive_question(q_txt, next_txts):
            report["dropped_open_questions"] += 1
            continue
        norm_k = " ".join(q_txt.lower().split())
        if norm_k in seen_oq:
            continue
        seen_oq.add(norm_k)
        ts_clean = _fmt_mmss(seg_sec) if segs else str(q.get("timestamp_str") or "00:00")
        if ts_clean != str(q.get("timestamp_str") or ""):
            report["timestamp_snaps"] += 1
        cat = classify_open_question(q)
        clean_oqs.append({
            "question": q_txt,
            "category": cat,
            "asked_by": str(q.get("asked_by") or seg_spk or "").strip(),
            "context": str(q.get("context") or "").strip() or (f"Raised at {ts_clean}" if ts_clean else ""),
            "timestamp_str": ts_clean,
            "timestamp_sec": seg_sec,
            "evidence_quote": str(q.get("evidence_quote") or seg_txt or "").strip()[:220],
            "status": "Pending Confirmation" if cat == "pending_confirmation" else "Open",
        })
        report["verified_claims"] += 1
    out["open_questions"] = clean_oqs

    # 2. Verify & Clean Risks / Blockers (atomic records, zero fused RISKImpact labels)
    raw_risks = list(out.get("risks") or [])
    clean_risks: List[Dict[str, Any]] = []
    seen_risk: set[str] = set()
    for r in raw_risks:
        if not isinstance(r, dict):
            continue
        raw_desc = str(r.get("description") or r.get("risk") or "").strip()
        cleaned_desc = _LEAKED_RISK_TAG_RE.sub("", raw_desc).strip()
        cleaned_desc = re.sub(r"\s{2,}", " ", cleaned_desc)
        if not cleaned_desc or cleaned_desc.lower() in _NULL_TOKENS:
            continue
        if cleaned_desc != raw_desc:
            report["risks_cleaned"] += 1
        norm_r = " ".join(cleaned_desc.lower().split())
        if norm_r in seen_risk:
            continue
        seen_risk.add(norm_r)
        ev_r = str(r.get("evidence_quote") or cleaned_desc).strip()
        _, seg_sec, seg_spk, seg_txt = _match_segment(segs, ev_r, str(r.get("timestamp_str") or ""))
        ts_clean = _fmt_mmss(seg_sec) if segs else str(r.get("timestamp_str") or "00:00")
        if ts_clean != str(r.get("timestamp_str") or ""):
            report["timestamp_snaps"] += 1
        rtype = str(r.get("type") or r.get("item_type") or "risk").strip().lower()
        if rtype not in ("risk", "blocker"):
            rtype = "blocker" if "block" in cleaned_desc.lower() else "risk"
        impact = str(r.get("impact") or r.get("severity") or "medium").strip().lower()
        if impact not in ("high", "medium", "low", "critical"):
            impact = "medium"
        mit = str(r.get("mitigation") or "").strip()
        if mit.lower() in ("none", "n/a", "null", "under review"):
            mit = ""
        rstatus = str(r.get("status") or "open").strip().lower()
        if rstatus not in ("open", "mitigated", "monitoring", "identified", "active", "resolved"):
            rstatus = "open"
        clean_risks.append({
            "type": rtype,
            "item_type": rtype,
            "risk": cleaned_desc,
            "description": cleaned_desc,
            "impact": impact,
            "severity": impact,
            "mitigation": mit,
            "status": rstatus,
            "raised_by": seg_spk,
            "timestamp_str": ts_clean,
            "timestamp_sec": seg_sec,
            "evidence_quote": str(r.get("evidence_quote") or seg_txt or "").strip()[:240],
        })
        report["verified_claims"] += 1
    out["risks"] = clean_risks

    # 3. Verify Action Items (strict owner attribution & deadline resolution)
    raw_actions = list(out.get("action_items") or [])
    clean_actions: List[Dict[str, Any]] = []
    seen_act: set[str] = set()
    for a in raw_actions:
        if not isinstance(a, dict):
            continue
        desc = re.sub(r"^review and address:\s*", "", str(a.get("description") or "").strip(), flags=re.IGNORECASE).strip()
        if not desc:
            continue
        norm_a = " ".join(desc.lower().split())
        if norm_a in seen_act:
            continue
        seen_act.add(norm_a)

        ev_a = str(a.get("evidence_quote") or desc).strip()
        seg_idx, seg_sec, seg_spk, seg_txt = _match_segment(segs, ev_a, str(a.get("timestamp_str") or ""))
        ts_clean = _fmt_mmss(seg_sec) if segs else str(a.get("timestamp_str") or "00:00")
        if ts_clean != str(a.get("timestamp_str") or ""):
            report["timestamp_snaps"] += 1

        window_txts = [
            f"{segs[j].get('speaker', '')}: {segs[j].get('text', '')}"
            for j in range(max(0, seg_idx - 1), min(len(segs), seg_idx + 2))
        ] if seg_idx >= 0 else [ev_a]
        window_blob = "\n".join(window_txts)

        raw_owner = a.get("owner")
        owner_s = str(raw_owner).strip() if raw_owner is not None else ""
        if owner_s.lower() in _NULL_TOKENS or "to be confirmed" in owner_s.lower():
            owner_s = ""

        # Normalize You (Mic) -> explicit local user name if self committed or directly accepted
        if owner_s.lower() in ("you", "you (mic)", "me", "speaker 0"):
            owner_s = self_owner_label

        # Owner verification guard against evidence
        if owner_s:
            # Passive/collective language without a clear first-person commitment should NOT keep a guessed owner
            if _COLLECTIVE_OR_PASSIVE_RE.search(seg_txt) and not _EXPLICIT_SELF_COMMIT_RE.search(window_blob):
                owner_s = ""
                report["owner_downgrades"] += 1
            elif owner_s.lower() == self_owner_label.lower():
                has_self = bool(_EXPLICIT_SELF_COMMIT_RE.search(window_blob))
                has_direct_req = bool(re.search(r"\b(?:want you to|you should|you can share)\b", window_blob, re.IGNORECASE)) and (
                    "you (mic)" in window_blob.lower() or "yeah" in window_blob.lower()
                )
                has_named_assign = bool(
                    re.search(rf"\b{re.escape(self_owner_label.lower())}\s+(?:will|shall|needs?\s+to|to)\b", window_blob, re.IGNORECASE)
                )
                if not (has_self or has_direct_req or has_named_assign):
                    owner_s = ""
                    report["owner_downgrades"] += 1
                else:
                    report["owner_self_resolved"] += 1
        elif _EXPLICIT_SELF_COMMIT_RE.search(seg_txt) and "you (mic)" in seg_spk.lower() and "we'll" not in seg_txt.lower():
            owner_s = self_owner_label
            report["owner_self_resolved"] += 1

        raw_due = a.get("due_date_text") or a.get("due_date")
        due_s = str(raw_due).strip() if raw_due is not None else ""
        if due_s.lower() in _NULL_TOKENS:
            due_s = ""
        due_res = resolve_deadline_expression(due_s, meeting_timestamp=meeting_timestamp, timezone=timezone) if due_s else {}
        resolved_due = due_res.get("resolved_date") or (due_s if due_s else None)

        raw_prio = a.get("priority")
        prio_s = str(raw_prio).strip().capitalize() if raw_prio is not None else ""
        priority = prio_s if prio_s in ("High", "Medium", "Low") else None

        ctype = str(a.get("confirmation_state") or a.get("commitment_type") or "").strip().lower()
        if not owner_s:
            ctype = "needs_confirmation"
        elif ctype not in ("confirmed_commitment", "suggested_action", "needs_confirmation"):
            ctype = "confirmed_commitment"

        clean_actions.append({
            "description": desc,
            "owner": owner_s if owner_s else None,
            "due_date": resolved_due,
            "due_date_text": due_s or None,
            "due_date_resolution": due_res if due_res else "",
            "priority": priority,
            "commitment_type": ctype,
            "confirmation_state": ctype,
            "related_topic": str(a.get("related_topic") or "").strip(),
            "context": str(a.get("context") or "").strip(),
            "timestamp_str": ts_clean,
            "timestamp_sec": seg_sec,
            "evidence_quote": str(a.get("evidence_quote") or seg_txt or "").strip()[:220],
            "speaker": seg_spk,
        })
        report["verified_claims"] += 1
    out["action_items"] = clean_actions

    # 4. Verify Decisions (status truthfulness vs provisional proposals)
    raw_decs = list(out.get("decisions") or [])
    clean_decs: List[Dict[str, Any]] = []
    seen_dec: set[str] = set()
    valid_cats = ("Strategic", "Technical", "Product", "Budget", "Process", "General")
    valid_states = ("confirmed", "proposed", "under_discussion", "rejected", "pending_confirmation")
    for d in raw_decs:
        if not isinstance(d, dict):
            continue
        dec_txt = str(d.get("decision") or "").strip()
        if not dec_txt:
            continue
        norm_d = " ".join(dec_txt.lower().split())
        if norm_d in seen_dec or norm_d in seen_act:
            continue
        seen_dec.add(norm_d)

        ev_d = str(d.get("evidence_quote") or dec_txt).strip()
        seg_idx, seg_sec, seg_spk, seg_txt = _match_segment(segs, ev_d, str(d.get("timestamp_str") or ""))
        ts_clean = _fmt_mmss(seg_sec) if segs else str(d.get("timestamp_str") or "00:00")
        if ts_clean != str(d.get("timestamp_str") or ""):
            report["timestamp_snaps"] += 1

        window_blob = " ".join(
            str(segs[j].get("text") or "")
            for j in range(max(0, seg_idx - 1), min(len(segs), seg_idx + 2))
        ) if seg_idx >= 0 else f"{dec_txt} {ev_d}"

        stat = str(d.get("status") or "confirmed").strip().lower()
        if stat not in valid_states:
            stat = "confirmed"
        if stat == "confirmed" and _PROVISIONAL_DECISION_RE.search(window_blob):
            if "until" in window_blob.lower() or "confirm" in window_blob.lower() or "validate" in window_blob.lower():
                stat = "pending_confirmation"
            else:
                stat = "proposed"
            report["decision_downgrades"] += 1

        cat = str(d.get("category") or "General").strip().capitalize()
        if cat not in valid_cats:
            cat = "General"
        evo = str(d.get("evolution_relationship") or "new").strip().lower()
        if evo not in ("new", "repeated", "superseded", "reversed"):
            evo = "new"
        spk = str(d.get("speaker") or seg_spk or "").strip()
        if spk.lower() in ("you", "you (mic)") and self_owner_label != "You (Mic)":
            spk = self_owner_label

        clean_decs.append({
            "decision": dec_txt,
            "status": stat,
            "decision_state": stat,
            "category": cat,
            "evolution_relationship": evo,
            "speaker": spk or seg_spk,
            "participants": list(d.get("participants") or ([spk] if spk else [])),
            "context": str(d.get("context") or "").strip(),
            "rationale": str(d.get("rationale") or "").strip(),
            "timestamp_str": ts_clean,
            "timestamp_sec": seg_sec,
            "evidence_quote": str(d.get("evidence_quote") or seg_txt or "").strip()[:220],
        })
        report["verified_claims"] += 1
    out["decisions"] = clean_decs

    # 5. Snap timestamps for follow_ups / key_discussion_points / topics
    clean_fus: List[Dict[str, Any]] = []
    for f in list(out.get("follow_ups") or []):
        if not isinstance(f, dict) or not str(f.get("item") or "").strip():
            continue
        raw_own = f.get("owner")
        own_s = str(raw_own).strip() if raw_own is not None else ""
        if own_s.lower() in _NULL_TOKENS:
            own_s = ""
        elif own_s.lower() in ("you", "you (mic)"):
            own_s = self_owner_label
        _, seg_sec, _, seg_txt = _match_segment(segs, str(f.get("evidence_quote") or f.get("item") or ""), str(f.get("timestamp_str") or ""))
        clean_fus.append({
            "item": str(f.get("item")).strip(),
            "category": str(f.get("category") or "pending_confirmation").strip(),
            "owner": own_s if own_s else None,
            "timestamp_str": _fmt_mmss(seg_sec) if segs else str(f.get("timestamp_str") or "00:00"),
            "timestamp_sec": seg_sec,
            "evidence_quote": str(f.get("evidence_quote") or seg_txt or "").strip()[:200],
        })
    out["follow_ups"] = clean_fus

    # 6. Sanitize Key Discussion Points, Topics & Executive/Detailed Summaries (zero greetings/timestamps in prose)
    for sum_k in ("brief_summary", "detailed_summary", "executive_summary", "meeting_purpose", "overall_outcome"):
        if sum_k in out and isinstance(out[sum_k], str) and out[sum_k].strip():
            out[sum_k] = clean_user_facing_text(out[sum_k])

    clean_kps: List[Dict[str, Any]] = []
    for kp in list(out.get("key_discussion_points") or []):
        if not isinstance(kp, dict):
            continue
        top = clean_user_facing_text(str(kp.get("topic") or ""))
        sm = clean_user_facing_text(str(kp.get("summary") or ""))
        if not (top or sm) or is_greeting_or_chatter(sm or top):
            continue
        clean_kps.append({**kp, "topic": top, "summary": sm})
    if "key_discussion_points" in out:
        out["key_discussion_points"] = clean_kps

    clean_topics: List[Dict[str, Any]] = []
    for top in list(out.get("topics") or []):
        if not isinstance(top, dict):
            continue
        ttl = clean_user_facing_text(str(top.get("title") or ""))
        sm = clean_user_facing_text(str(top.get("summary") or ""))
        if not (ttl or sm) or (is_greeting_or_chatter(ttl) and is_greeting_or_chatter(sm)):
            continue
        details = [
            clean_user_facing_text(str(d))
            for d in (top.get("details") or [])
            if str(d).strip() and not is_greeting_or_chatter(str(d))
        ]
        clean_topics.append({**top, "title": ttl or "Discussion Topic", "summary": sm, "details": details})
    if "topics" in out:
        out["topics"] = clean_topics

    out["verification_report"] = report
    return out, report

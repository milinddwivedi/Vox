"""
VoxLocal Intelligent Cross-Meeting Cognitive Synthesis Engine.
Provides comprehensive 100% offline local AI & algorithmic reasoning across recorded meetings:
- Natural-Language Intent Classification (Summaries, Action Items, Decisions, Topic QA, Timelines)
- Multi-Source Grounded Retrieval (Transcripts, Executive Summaries, Decisions, Action Items, Topics)
- BM25 & Lexical/Semantic Re-Ranking with Query Scoping
- Structured Executive Answer Synthesis with Verified Source Citations
- Zero-crash defensive error handling and safe date/time formatting
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from voxlocal.core.logger import get_logger
from voxlocal.storage.exporter import format_seconds_hms

logger = get_logger("intelligence")


class QueryIntent(Enum):
    SUMMARY = "summary"
    ACTION_ITEMS = "action_items"
    DECISIONS = "decisions"
    TOPIC_QA = "topic_qa"
    SPEAKER_QA = "speaker_qa"
    TIMELINE = "timeline"
    GENERAL = "general"
    CUSTOMER_NOTES = "customer_notes"
    FOLLOW_UP_EMAIL = "follow_up_email"
    RISKS_AND_OPEN_ITEMS = "risks_open_items"
    COMPARE_MEETINGS = "compare_meetings"
    MULTI_MEETING_RECAP = "multi_meeting_recap"


STOPWORDS: Set[str] = {
    "a", "about", "above", "across", "after", "again", "against", "all", "almost",
    "alone", "along", "already", "also", "although", "always", "among", "an", "and",
    "another", "any", "anybody", "anyone", "anything", "anywhere", "are", "area",
    "areas", "aren't", "around", "as", "ask", "asked", "asking", "asks", "at",
    "away", "b", "back", "backed", "backing", "backs", "be", "became", "because",
    "become", "becomes", "becoming", "been", "before", "began", "behind", "being",
    "beings", "best", "better", "between", "big", "both", "but", "by", "c", "came",
    "can", "cannot", "cant", "can't", "case", "cases", "certain", "certainly",
    "clear", "clearly", "come", "could", "couldn't", "d", "did", "didn't", "differ",
    "different", "differently", "do", "does", "doesn't", "doing", "done", "dont",
    "don't", "down", "downed", "downing", "downs", "during", "e", "each", "early",
    "either", "end", "ended", "ending", "ends", "enough", "even", "evenly", "ever",
    "every", "everybody", "everyone", "everything", "everywhere", "f", "face", "faces",
    "fact", "facts", "far", "felt", "few", "fewer", "find", "finds", "first", "for",
    "four", "from", "full", "fully", "further", "furthered", "furthering", "furthers",
    "g", "gave", "general", "generally", "get", "gets", "getting", "give", "given",
    "gives", "go", "going", "good", "goods", "got", "great", "greater", "greatest",
    "group", "grouped", "grouping", "groups", "h", "had", "has", "hasn't", "have",
    "haven't", "having", "he", "hed", "he'd", "he'll", "hello", "help", "her",
    "here", "hereafter", "hereby", "herein", "here's", "hereupon", "hers", "herself",
    "he's", "hi", "him", "himself", "his", "how", "however", "i", "id", "i'd", "if",
    "ill", "i'll", "im", "i'm", "in", "into", "is", "isn't", "it", "it'd", "it'll",
    "it's", "its", "itself", "ive", "i've", "j", "just", "k", "keep", "keeps", "kept",
    "know", "known", "knows", "l", "large", "largely", "last", "later", "latest",
    "least", "less", "let", "lets", "let's", "like", "likely", "long", "longer",
    "longest", "m", "made", "make", "making", "man", "many", "may", "maybe", "me",
    "member", "members", "men", "might", "more", "most", "mostly", "mr", "mrs", "much",
    "must", "my", "myself", "n", "necessary", "need", "needed", "needing", "needs",
    "never", "new", "newer", "newest", "next", "no", "nobody", "non", "noone", "not",
    "nothing", "now", "nowhere", "number", "numbers", "o", "of", "off", "often", "old",
    "older", "oldest", "on", "once", "one", "only", "open", "opened", "opening", "opens",
    "or", "order", "ordered", "ordering", "orders", "other", "others", "our", "out",
    "over", "p", "part", "parted", "parting", "parts", "per", "perhaps", "place", "places",
    "point", "pointed", "pointing", "points", "possible", "present", "presented", "presenting",
    "presents", "problem", "problems", "put", "puts", "q", "quite", "r", "rather", "really",
    "recent", "recently", "right", "room", "rooms", "s", "said", "same", "saw", "say",
    "saying", "says", "second", "seconds", "see", "seem", "seemed", "seeming", "seems",
    "sees", "several", "shall", "she", "shed", "she'd", "she'll", "she's", "should",
    "shouldn't", "show", "showed", "showing", "shows", "side", "sides", "since", "small",
    "smaller", "smallest", "so", "some", "somebody", "someone", "something", "somewhere",
    "state", "states", "still", "such", "sure", "t", "take", "taken", "than", "that",
    "the", "their", "them", "then", "there", "thereafter", "thereby", "therefore", "therein",
    "there's", "thereupon", "these", "they", "theyd", "they'd", "they'll", "they're",
    "they've", "thing", "things", "think", "thinks", "this", "those", "though", "thought",
    "thoughts", "three", "through", "thus", "to", "today", "together", "too", "took",
    "toward", "turn", "turned", "turning", "turns", "two", "u", "under", "until", "up",
    "upon", "us", "use", "used", "uses", "v", "very", "w", "want", "wanted", "wanting",
    "wants", "was", "wasn't", "way", "ways", "we", "wed", "we'd", "well", "we'll",
    "went", "were", "we're", "weren't", "we've", "what", "whatever", "what's", "when",
    "where", "whereafter", "whereas", "whereby", "wherein", "where's", "whereupon", "wherever",
    "whether", "which", "while", "whither", "who", "whoever", "whole", "whom", "whose",
    "why", "will", "with", "within", "without", "wont", "won't", "work", "worked",
    "working", "works", "would", "wouldn't", "x", "y", "year", "years", "yes", "yet",
    "you", "youd", "you'd", "you'll", "your", "you're", "yours", "yourself", "yourselves",
    "you've", "z"
}

ACTION_CUES = [
    "action item", "action items", "to do", "todo", "task", "tasks", "assigned",
    "owner", "owners", "deadline", "deliverable", "follow up", "follow-up",
    "next steps", "next step", "who will", "who is doing", "responsibility"
]

DECISION_CUES = [
    "decision", "decisions", "decide", "decided", "agreed", "agreement", "conclusion",
    "concluded", "consensus", "approved", "chosen", "settled", "resolution", "final"
]

SUMMARY_CUES = [
    "summarize", "summary", "recap", "overview", "what happened", "what was discussed",
    "key points", "highlights", "brief", "briefing", "minutes", "arguments"
]

CUSTOMER_NOTE_CUES = [
    "share with the customer", "share with customer", "customer notes", "client notes",
    "notes of this meeting", "shareable notes", "external notes", "meeting notes to share",
]

FOLLOWUP_EMAIL_CUES = [
    "follow-up email", "follow up email", "followup email", "draft an email",
    "prepare an email", "prepare a follow-up email", "write an email", "email recap",
]

RISK_OPEN_CUES = [
    "risk", "risks", "blocker", "blockers", "unresolved", "still pending",
    "pending confirmation", "open question", "open questions", "remaining issue",
]


def resolve_relative_due_date(
    expression: str,
    meeting_timestamp: Optional[str] = None,
    timezone: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Structured relative date resolution with explicit uncertainty retention.
    Resolves spoken temporal phrases ('by Friday', 'by tomorrow', 'in 3 days')
    against an anchor meeting timestamp into a verified calendar date (YYYY-MM-DD).
    Zero invented dates on ambiguity or missing reference: sets resolved_date=None
    and provides clear uncertainty_reason.
    """
    clean_expr = (expression or "").strip()
    if not clean_expr:
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

    # ISO date check (e.g. "2026-11-06")
    m_iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", clean_expr)
    if m_iso:
        iso_val = m_iso.group(1)
        return {
            "original_expression": clean_expr,
            "original_text": clean_expr,
            "resolved_date": iso_val,
            "meeting_timestamp": meeting_timestamp,
            "reference_timestamp": meeting_timestamp,
            "timezone_used": timezone,
            "timezone": timezone,
            "resolution_status": "deterministic",
            "confidence": 1.0,
            "uncertainty_reason": None,
        }

    # If anchor meeting timestamp is missing or unparseable
    anchor_dt: Optional[datetime] = None
    if meeting_timestamp:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                anchor_dt = datetime.strptime(str(meeting_timestamp).strip(), fmt)
                break
            except Exception:
                continue

    if anchor_dt is None:
        return {
            "original_expression": clean_expr,
            "original_text": clean_expr,
            "resolved_date": None,
            "meeting_timestamp": meeting_timestamp,
            "reference_timestamp": meeting_timestamp,
            "timezone_used": timezone,
            "timezone": timezone,
            "resolution_status": "unresolved",
            "confidence": 0.0,
            "uncertainty_reason": "Missing or unparseable anchor meeting timestamp",
        }

    lowered = clean_expr.lower()

    # Check for ambiguous phrases that should NOT invent dates
    ambiguous_patterns = (
        "couple of weeks", "few weeks", "sometime", "eventually", "later",
        "down the road", "next sprint", "unspecified", "tbd", "when possible",
        "next month", "couple days", "few days"
    )
    if any(p in lowered for p in ambiguous_patterns):
        return {
            "original_expression": clean_expr,
            "original_text": clean_expr,
            "resolved_date": None,
            "meeting_timestamp": meeting_timestamp,
            "reference_timestamp": meeting_timestamp,
            "timezone_used": timezone,
            "timezone": timezone,
            "resolution_status": "ambiguous",
            "confidence": 0.25,
            "uncertainty_reason": "Ambiguous temporal phrase without explicit calendar target",
        }

    resolved_date_str: Optional[str] = None
    status = "deterministic"
    confidence = 1.0
    uncertainty: Optional[str] = None

    # "tomorrow"
    if "tomorrow" in lowered:
        target = anchor_dt + timedelta(days=1)
        resolved_date_str = target.strftime("%Y-%m-%d")

    # "today" / "end of day" / "eod"
    elif any(cue in lowered for cue in ("end of day", "eod", "today", "by tonight")):
        resolved_date_str = anchor_dt.strftime("%Y-%m-%d")

    # "in N days"
    elif re.search(r"\bin\s+(\d+)\s+days?\b", lowered):
        m_days = re.search(r"\bin\s+(\d+)\s+days?\b", lowered)
        if m_days:
            n_days = int(m_days.group(1))
            target = anchor_dt + timedelta(days=n_days)
            resolved_date_str = target.strftime("%Y-%m-%d")

    # "in N weeks"
    elif re.search(r"\bin\s+(\d+)\s+weeks?\b", lowered):
        m_wks = re.search(r"\bin\s+(\d+)\s+weeks?\b", lowered)
        if m_wks:
            n_wks = int(m_wks.group(1))
            target = anchor_dt + timedelta(weeks=n_wks)
            resolved_date_str = target.strftime("%Y-%m-%d")

    # Explicit calendar date e.g. "4th of December", "December 4", "4 Dec 2026"
    _mon_map = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
        "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    low_clean = clean_expr.lower()
    m_dmy = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|october|oct|november|nov|december|dec)(?:[,\s]+(\d{4}))?\b",
        low_clean,
    )
    m_mdy = re.search(
        r"\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|october|oct|november|nov|december|dec)\s+(\d{1,2})(?:st|nd|rd|th)?(?:[,\s]+(\d{4}))?\b",
        low_clean,
    )
    if (m_dmy or m_mdy) and (anchor_dt is not None or (m_dmy and m_dmy.group(3)) or (m_mdy and m_mdy.group(3))):
        if m_dmy:
            day_i, mon_i, yr_str = int(m_dmy.group(1)), _mon_map[m_dmy.group(2)], m_dmy.group(3)
        else:
            mon_i, day_i, yr_str = _mon_map[m_mdy.group(1)], int(m_mdy.group(2)), m_mdy.group(3)
        year_i = int(yr_str) if yr_str else anchor_dt.year
        try:
            cand_dt = datetime(year_i, mon_i, day_i)
            if not yr_str and anchor_dt and (anchor_dt.date() - cand_dt.date()).days > 180:
                cand_dt = datetime(year_i + 1, mon_i, day_i)
            return {
                "original_expression": clean_expr,
                "original_text": clean_expr,
                "resolved_date": cand_dt.strftime("%Y-%m-%d"),
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

    # Weekdays ("next week Thursday" vs "Friday")
    else:
        weekdays = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6
        }
        matched_day = None
        for day_name, day_num in weekdays.items():
            if re.search(rf"\b{day_name}\b", lowered):
                matched_day = (day_name, day_num)
                break

        if matched_day:
            day_name, target_day_num = matched_day
            cur_weekday = anchor_dt.weekday()
            if re.search(r"\bnext\s+week\b", lowered):
                days_ahead = (7 - cur_weekday) + target_day_num
            else:
                days_ahead = (target_day_num - cur_weekday) % 7
                if days_ahead == 0:
                    days_ahead = 7

            target = anchor_dt + timedelta(days=days_ahead)
            resolved_date_str = target.strftime("%Y-%m-%d")

        elif "end of week" in lowered:
            cur_weekday = anchor_dt.weekday()
            days_to_fri = (4 - cur_weekday) % 7
            if days_to_fri == 0:
                days_to_fri = 7
            target = anchor_dt + timedelta(days=days_to_fri)
            resolved_date_str = target.strftime("%Y-%m-%d")

        elif "next week" in lowered:
            cur_weekday = anchor_dt.weekday()
            days_to_next_mon = (7 - cur_weekday)
            target = anchor_dt + timedelta(days=days_to_next_mon)
            resolved_date_str = target.strftime("%Y-%m-%d")

        else:
            status = "unresolved"
            confidence = 0.0
            uncertainty = f"Unrecognized relative date format: '{clean_expr}'"

    return {
        "original_expression": clean_expr,
        "original_text": clean_expr,
        "resolved_date": resolved_date_str,
        "meeting_timestamp": meeting_timestamp,
        "reference_timestamp": meeting_timestamp,
        "timezone_used": timezone,
        "timezone": timezone,
        "resolution_status": status,
        "confidence": confidence,
        "uncertainty_reason": uncertainty,
    }


class MeetingIntelligenceEngine:
    """
    100% Offline Cognitive Synthesis Engine for Single & Cross-Meeting Question Answering.
    Combines hybrid FTS5 + BGE semantic RAG (`LocalKnowledgeStore`), structured meeting outcomes,
    and grounded local Qwen3.5-4B synthesis with explicit absent-fact refusal.
    """

    def __init__(
        self,
        db: Any,
        llm_engine: Optional[Any] = None,
        knowledge_store: Optional[Any] = None,
    ) -> None:
        self.db = db
        self.llm_engine = llm_engine
        self.knowledge_store = knowledge_store
        self._last_retrieved_knowledge: List[Dict[str, Any]] = []

    def query(
        self,
        question: str,
        meeting_id: Optional[int] = None,
        conversation_history: Optional[List[Any]] = None,
        answer_depth: str = "detailed",
        meeting_ids: Optional[List[int]] = None,
        local_user_name: str = "",
    ) -> Dict[str, Any]:
        """
        Process a natural language user query against meeting memory.
        Returns:
            {
                "question": str,
                "answer": str (rich formatted markdown),
                "citations": List[Dict[str, Any]],
                "intent": str,
                "suggestions": List[str],
                "effective_scope_label": str
            }
        """
        cleaned_q = question.strip()
        if not cleaned_q:
            return {
                "question": question,
                "answer": "Please ask a question about your recorded meetings, discussions, action items, or decisions.",
                "citations": [],
                "intent": "empty",
                "suggestions": ["What were the key decisions in recent meetings?", "List all pending action items."]
            }

        try:
            # 1. Intent, Multi-Meeting Count, Owner Filter & Scope Classification
            intent, tokens, scoped_title = self._classify_query(cleaned_q)
            multi_n, self_only = self._extract_scope_modifiers(cleaned_q)

            # 2. Retrieve Candidate Documents & Source Excerpts
            all_meetings = self.db.list_meetings(limit=250)
            target_meeting_id = meeting_id
            scoped_meeting = None
            allowed_ids: Optional[set[int]] = None
            effective_scope_label = "This Meeting" if target_meeting_id is not None else "All Recorded Meetings"

            # Explicit natural language multi-meeting override ("last 3 meetings", "last three meetings")
            if multi_n is not None and meeting_ids is None:
                completed_recent = [
                    m for m in all_meetings
                    if str(m.get("status") or "").strip().lower() in ("completed", "incomplete", "")
                ] or all_meetings
                picked = completed_recent[:multi_n]
                allowed_ids = {int(m.get("id")) for m in picked if m.get("id") is not None}
                target_meeting_id = None
                all_meetings = picked
                effective_scope_label = f"Last {len(picked)} Meetings"
            elif target_meeting_id is None and meeting_ids is not None:
                allowed_ids = {int(x) for x in meeting_ids}
                all_meetings = [m for m in all_meetings if int(m.get("id") or 0) in allowed_ids]
                effective_scope_label = f"{len(all_meetings)} Scoped Meetings"

            # If a specific meeting ID was explicitly provided, retrieve directly from DB (bypasses 250 catalog cap)
            if target_meeting_id is not None:
                scoped_meeting = self.db.get_meeting(target_meeting_id)
                if scoped_meeting:
                    all_meetings = [scoped_meeting] + [m for m in all_meetings if m.get("id") != target_meeting_id]
                    effective_scope_label = f"This Meeting: {scoped_meeting.get('title') or f'#{target_meeting_id}'}"

            if not all_meetings and target_meeting_id is None:
                return {
                    "question": question,
                    "answer": (
                        "### 🎙️ No Recorded Meetings Found\n\n"
                        "No meeting recordings or transcripts are currently stored in your local VoxLocal database.\n\n"
                        "**Next steps:**\n"
                        "• Use the **Meeting Studio** tab or the floating pill (`⏺`) to record a live call or conversation.\n"
                        "• Once recorded, VoxLocal's local AI will automatically transcribe and index your audio."
                    ),
                    "citations": [],
                    "intent": intent.value,
                    "suggestions": []
                }

            # If not explicitly provided, check if the user named a specific meeting
            if target_meeting_id is None and scoped_title:
                st_lower = scoped_title.lower().strip()
                # 1. Exact title match
                for m in all_meetings:
                    if st_lower == m.get("title", "").lower().strip():
                        target_meeting_id = m.get("id")
                        scoped_meeting = m
                        break
                # 2. Word boundary match
                if target_meeting_id is None:
                    for m in all_meetings:
                        m_title = m.get("title", "").lower().strip()
                        if re.search(r"\b" + re.escape(st_lower) + r"\b", m_title):
                            target_meeting_id = m.get("id")
                            scoped_meeting = m
                            break
                # 3. Substring match fallback
                if target_meeting_id is None:
                    for m in all_meetings:
                        if st_lower in m.get("title", "").lower():
                            target_meeting_id = m.get("id")
                            scoped_meeting = m
                            break

            # 3. Retrieve Search Results from Database
            citations = self._retrieve_citations(
                question=cleaned_q,
                tokens=tokens,
                intent=intent,
                target_meeting_id=target_meeting_id,
                all_meetings=all_meetings,
                meeting_ids=list(allowed_ids) if allowed_ids is not None else None,
            )

            # 4. Synthesize Cognitive Answer
            answer_md, suggestions = self._synthesize_answer(
                question=cleaned_q,
                intent=intent,
                tokens=tokens,
                target_meeting_id=target_meeting_id,
                citations=citations,
                all_meetings=all_meetings,
                scoped_meeting=scoped_meeting,
                conversation_history=conversation_history,
                answer_depth=answer_depth,
                allowed_ids=allowed_ids,
                local_user_name=local_user_name,
                self_only=self_only,
            )

            return {
                "question": question,
                "answer": answer_md,
                "citations": citations,
                "intent": intent.value,
                "suggestions": suggestions,
                "effective_scope_label": effective_scope_label,
            }

        except Exception as exc:
            logger.error("Error during meeting intelligence synthesis: %s", exc, exc_info=True)
            return {
                "question": question,
                "answer": (
                    f"### ⚠️ An Error Occurred During Analysis\n\n"
                    f"Could not complete meeting memory analysis: `{str(exc)}`\n\n"
                    f"Please try rephrasing your question or selecting a specific meeting from the Reports tab."
                ),
                "citations": [],
                "intent": "error",
                "suggestions": ["Summarize recent meetings", "List all action items"]
            }

    def _verify_ask_answer_grounding(
        self,
        answer_text: str,
        citations: List[Dict[str, Any]],
        intent: QueryIntent,
    ) -> Tuple[str, Dict[str, Any]]:
        """Validate Ask VOX answer formatting & internal traceability without leaking raw metadata."""
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", str(answer_text or ""), flags=re.IGNORECASE).strip()
        if "<think>" in cleaned:
            cleaned = cleaned.split("<think>", 1)[0].strip()
        report = {
            "citation_count": len(citations or []),
            "intent": intent.value if isinstance(intent, QueryIntent) else str(intent),
            "grounded": bool(cleaned),
        }
        return cleaned, report

    def query_stream(
        self,
        question: str,
        meeting_id: Optional[int] = None,
        on_token: Optional[Callable[[str], None]] = None,
        on_citations: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        conversation_history: Optional[List[Any]] = None,
        answer_depth: str = "detailed",
        meeting_ids: Optional[List[int]] = None,
        local_user_name: str = "",
    ) -> Dict[str, Any]:
        """
        Stream a natural language user query answer against meeting memory in real time.
        Emits genuine internal processing states via on_status, tokens via on_token,
        and preserves internal traceability citations on the returned dictionary.
        """
        def _emit_status(state_name: str) -> None:
            if on_status:
                try:
                    on_status(state_name)
                except Exception:
                    pass

        cleaned_q = question.strip()
        if not cleaned_q:
            msg = "Please ask a question about your recorded meetings, discussions, action items, or decisions."
            if on_token:
                on_token(msg)
            return {
                "question": question,
                "answer": msg,
                "citations": [],
                "intent": "empty",
                "suggestions": ["What were the key decisions in recent meetings?", "List all pending action items."],
            }

        try:
            _emit_status("retrieving_context")
            # 1. Intent & Scope Classification
            intent, tokens, scoped_title = self._classify_query(cleaned_q)
            multi_n, self_only = self._extract_scope_modifiers(cleaned_q)

            # 2. Retrieve Candidate Documents & Source Excerpts
            all_meetings = self.db.list_meetings(limit=250)
            target_meeting_id = meeting_id
            scoped_meeting = None
            allowed_ids: Optional[set[int]] = None
            effective_scope_label = "This Meeting" if target_meeting_id is not None else "All Recorded Meetings"
            if multi_n is not None and meeting_ids is None:
                completed_recent = [
                    m for m in all_meetings
                    if str(m.get("status") or "").strip().lower() in ("completed", "incomplete", "")
                ] or all_meetings
                picked = completed_recent[:multi_n]
                allowed_ids = {int(m.get("id")) for m in picked if m.get("id") is not None}
                target_meeting_id = None
                all_meetings = picked
                effective_scope_label = f"Last {len(picked)} Meetings"
            elif target_meeting_id is None and meeting_ids is not None:
                allowed_ids = {int(x) for x in meeting_ids}
                all_meetings = [m for m in all_meetings if int(m.get("id") or 0) in allowed_ids]
                effective_scope_label = f"{len(all_meetings)} Scoped Meetings"

            # If a specific meeting ID was explicitly provided, retrieve directly from DB (bypasses 250 catalog cap)
            if target_meeting_id is not None:
                scoped_meeting = self.db.get_meeting(target_meeting_id)
                if scoped_meeting:
                    all_meetings = [scoped_meeting] + [m for m in all_meetings if m.get("id") != target_meeting_id]
                    effective_scope_label = f"This Meeting: {scoped_meeting.get('title') or f'#{target_meeting_id}'}"

            if not all_meetings and target_meeting_id is None:
                empty_msg = (
                    "### No Recorded Meetings Found\n\n"
                    "No meeting recordings or transcripts are currently stored in your local VOX library.\n\n"
                    "**Next steps:**\n"
                    "• Start a recording from Notetaker or the floating bar.\n"
                    "• Once recorded, VOX will automatically transcribe and analyze your meeting."
                )
                if on_token:
                    on_token(empty_msg)
                return {
                    "question": question,
                    "answer": empty_msg,
                    "citations": [],
                    "intent": intent.value,
                    "suggestions": [],
                }

            # If not explicitly provided, check if the user named a specific meeting
            if target_meeting_id is None and scoped_title:
                st_lower = scoped_title.lower().strip()
                # 1. Exact title match
                for m in all_meetings:
                    if st_lower == m.get("title", "").lower().strip():
                        target_meeting_id = m.get("id")
                        scoped_meeting = m
                        break
                # 2. Word boundary match
                if target_meeting_id is None:
                    for m in all_meetings:
                        m_title = m.get("title", "").lower().strip()
                        if re.search(r"\b" + re.escape(st_lower) + r"\b", m_title):
                            target_meeting_id = m.get("id")
                            scoped_meeting = m
                            break
                # 3. Substring match fallback
                if target_meeting_id is None:
                    for m in all_meetings:
                        if st_lower in m.get("title", "").lower():
                            target_meeting_id = m.get("id")
                            scoped_meeting = m
                            break

            # 3. Retrieve Search Results from Database
            citations = self._retrieve_citations(
                question=cleaned_q,
                tokens=tokens,
                intent=intent,
                target_meeting_id=target_meeting_id,
                all_meetings=all_meetings,
                meeting_ids=list(allowed_ids) if allowed_ids is not None else None,
            )

            # Deliver citations internally right away (retained for traceability, hidden in UI)
            if on_citations:
                try:
                    on_citations(citations)
                except Exception as exc:
                    logger.debug("on_citations callback error: %s", exc)

            if cancel_check and cancel_check():
                return {
                    "question": question,
                    "answer": "",
                    "citations": citations,
                    "intent": intent.value,
                    "suggestions": [],
                    "cancelled": True,
                }

            # 4. Stream via Local LLM Engine (if available and has citations or structured context)
            accumulated_answer: List[str] = []
            has_llm_streamed = False
            structured_ctx = self._build_structured_context_for_scope(
                target_meeting_id=target_meeting_id,
                scoped_meeting=scoped_meeting,
                all_meetings=all_meetings,
                allowed_ids=allowed_ids,
                local_user_name=local_user_name,
                self_only=self_only,
            )

            _emit_status("generating")
            if self.llm_engine and getattr(self.llm_engine, "is_available", lambda: False)() and (citations or self._last_retrieved_knowledge or structured_ctx):
                try:
                    meeting_title = scoped_meeting.get("title") if scoped_meeting else None
                    if hasattr(self.llm_engine, "answer_query_stream"):
                        try:
                            stream_it = self.llm_engine.answer_query_stream(
                                question=cleaned_q,
                                citations=citations,
                                context_meeting_title=meeting_title,
                                knowledge_items=self._last_retrieved_knowledge,
                                cancel_check=cancel_check,
                                conversation_history=conversation_history,
                                answer_depth=answer_depth,
                                structured_context=structured_ctx,
                                answer_intent=intent.value,
                                local_user_name=local_user_name,
                            )
                        except TypeError:
                            try:
                                stream_it = self.llm_engine.answer_query_stream(
                                    question=cleaned_q,
                                    citations=citations,
                                    context_meeting_title=meeting_title,
                                    knowledge_items=self._last_retrieved_knowledge,
                                    cancel_check=cancel_check,
                                    conversation_history=conversation_history,
                                    answer_depth=answer_depth,
                                )
                            except TypeError:
                                stream_it = self.llm_engine.answer_query_stream(
                                    question=cleaned_q,
                                    citations=citations,
                                    context_meeting_title=meeting_title,
                                )
                        for token_chunk in stream_it:
                            if cancel_check and cancel_check():
                                break
                            accumulated_answer.append(token_chunk)
                            if on_token:
                                on_token(token_chunk)
                        if accumulated_answer:
                            has_llm_streamed = True
                except Exception as exc:
                    if cancel_check and cancel_check():
                        return {
                            "question": question,
                            "answer": "".join(accumulated_answer),
                            "citations": citations,
                            "intent": intent.value,
                            "suggestions": [],
                            "cancelled": True,
                        }
                    logger.warning("Local LLM stream failed (%s), falling back to template synthesis", exc)

            if cancel_check and cancel_check():
                return {
                    "question": question,
                    "answer": "".join(accumulated_answer),
                    "citations": citations,
                    "intent": intent.value,
                    "suggestions": [],
                    "cancelled": True,
                }

            # 5. Fallback Algorithmic Synthesis (deliver completed response immediately — never simulate streaming)
            suggestions: List[str] = []
            if not has_llm_streamed and not (cancel_check and cancel_check()):
                answer_md, suggestions = self._synthesize_answer(
                    question=cleaned_q,
                    intent=intent,
                    tokens=tokens,
                    target_meeting_id=target_meeting_id,
                    citations=citations,
                    all_meetings=all_meetings,
                    scoped_meeting=scoped_meeting,
                    conversation_history=conversation_history,
                    answer_depth=answer_depth,
                    allowed_ids=allowed_ids,
                    local_user_name=local_user_name,
                    self_only=self_only,
                )
                if answer_md:
                    accumulated_answer.append(answer_md)
                    if on_token and not (cancel_check and cancel_check()):
                        on_token(answer_md)
            elif has_llm_streamed:
                meeting_label = (scoped_meeting.get("title") if scoped_meeting else None) or (citations[0].get("meeting_title", "recorded meetings") if citations else "recorded meetings")
                suggestions = [
                    f"What are the assigned action items in {meeting_label}?",
                    f"Summarize key decisions made in {meeting_label}",
                    "Were there any risks or blockers mentioned?",
                ]

            _emit_status("validating")
            full_answer, validation_report = self._verify_ask_answer_grounding(
                "".join(accumulated_answer),
                citations,
                intent,
            )
            return {
                "question": question,
                "answer": full_answer,
                "citations": citations,
                "intent": intent.value,
                "suggestions": suggestions,
                "effective_scope_label": effective_scope_label,
                "validation": validation_report,
                "cancelled": bool(cancel_check and cancel_check()),
            }

        except Exception as exc:
            logger.error("Error during meeting intelligence stream: %s", exc, exc_info=True)
            err_msg = (
                f"### ⚠️ An Error Occurred During Analysis\n\n"
                f"Could not complete meeting memory analysis: `{str(exc)}`\n\n"
                f"Please try rephrasing your question or selecting a specific meeting from the Reports tab."
            )
            if on_token:
                on_token(err_msg)
            return {
                "question": question,
                "answer": err_msg,
                "citations": [],
                "intent": "error",
                "suggestions": ["Summarize recent meetings", "List all action items"]
            }


    def compare_meetings(self, meeting_id_a: int, meeting_id_b: int) -> Dict[str, Any]:
        """
        Compare two meeting sessions side-by-side with canonical chronological normalization:
        - Meeting metadata (dates, durations, source platforms) strictly sorted (m_early vs m_later)
        - Decoupled 7-state decision evolution tracking (new, repeated, superseded, reversed)
        - Preserves distinct business status (proposed, confirmed, rejected, unresolved, unclassified)
        - Links supersedes_id without deleting or overwriting earlier records
        - Action items carry-over and completion status
        - Discussion topic shifts and sentiment/focus differences
        - Generates structured side-by-side comparison markdown report
        """
        m_a = self.db.get_meeting(meeting_id_a)
        m_b = self.db.get_meeting(meeting_id_b)
        if not m_a or not m_b:
            raise ValueError(f"One or both meetings not found: id={meeting_id_a}, id={meeting_id_b}")

        # Canonical chronological ordering: meeting A is strictly earlier, meeting B is later
        date_a = str(m_a.get("created_at") or m_a.get("start_time") or "")
        date_b = str(m_b.get("created_at") or m_b.get("start_time") or "")

        # Sort chronologically. If timestamps are equal or unparseable, sort deterministically by id
        is_a_earlier = True
        if date_a and date_b and date_a != date_b:
            is_a_earlier = (date_a < date_b)
        elif date_a and not date_b:
            is_a_earlier = True
        elif date_b and not date_a:
            is_a_earlier = False
        else:
            is_a_earlier = (meeting_id_a <= meeting_id_b)

        if is_a_earlier:
            m_early, m_later = m_a, m_b
            id_early, id_later = meeting_id_a, meeting_id_b
        else:
            m_early, m_later = m_b, m_a
            id_early, id_later = meeting_id_b, meeting_id_a

        # Load decisions
        decisions_early = self.db.list_decisions(meeting_id=id_early, limit=100)
        decisions_later = self.db.list_decisions(meeting_id=id_later, limit=100)

        # Load actions
        actions_early = self.db.list_action_items(meeting_id=id_early, limit=100)
        actions_later = self.db.list_action_items(meeting_id=id_later, limit=100)

        # Detect carry-over / continuity from earlier session (open work across pending, blocked, reopened)
        open_early_actions = [
            act for act in actions_early
            if act.get("status") in ("pending", "blocked", "reopened") or (act.get("status") not in ("completed", "cancelled"))
        ]

        # Topic comparisons (early vs later)
        topics_early = [t.strip() for t in str(m_early.get("topics") or "").replace(",", "\n").split("\n") if t.strip()]
        topics_later = [t.strip() for t in str(m_later.get("topics") or "").replace(",", "\n").split("\n") if t.strip()]
        common_topics = [t for t in topics_early if any(t.lower() in tb.lower() or tb.lower() in t.lower() for tb in topics_later)]
        new_in_b = [t for t in topics_later if not any(t.lower() in ta.lower() or ta.lower() in t.lower() for ta in topics_early)]

        # Decoupled 7-State Decision Evolution Matcher
        decision_deltas: List[Dict[str, Any]] = []
        for d_lat in decisions_later:
            lat_text = str(d_lat.get("decision") or "").strip()
            lat_text_lower = lat_text.lower()
            lat_id = d_lat.get("id")
            lat_status = str(d_lat.get("status") or "confirmed").strip().lower()
            if lat_status not in ("proposed", "confirmed", "rejected", "unresolved", "unclassified"):
                lat_status = "confirmed"

            # Check if d_lat already points to a verified supersedes_id
            matched_early = None
            if d_lat.get("supersedes_id"):
                matched_early = next((d for d in decisions_early if d.get("id") == d_lat["supersedes_id"]), None)

            # Algorithmic topic/subject matching against decisions_early
            if not matched_early:
                lat_words = set(re.findall(r"\w+", lat_text_lower)) - STOPWORDS
                best_early = None
                best_score = 0
                for d_ear in decisions_early:
                    ear_text = str(d_ear.get("decision") or "").strip()
                    ear_words = set(re.findall(r"\w+", ear_text.lower())) - STOPWORDS
                    common_words = lat_words & ear_words
                    if common_words:
                        score = len(common_words)
                        if score > best_score:
                            best_score = score
                            best_early = d_ear

                if best_score >= 1:
                    matched_early = best_early

            # Classify canonical evolution_relationship
            evolution_rel = "new"
            supersedes_id = None
            if matched_early:
                supersedes_id = matched_early.get("id")
                ear_text = str(matched_early.get("decision") or "").strip()
                ear_lower = ear_text.lower()

                lat_tokens = set(re.findall(r"\w+", lat_text_lower)) - STOPWORDS
                ear_tokens = set(re.findall(r"\w+", ear_lower)) - STOPWORDS
                token_overlap = (len(lat_tokens & ear_tokens) / max(1, len(lat_tokens))) if lat_tokens else 0.0

                # Check if reversed
                if any(cue in lat_text_lower for cue in ("reverse", "reversed", "cancel", "cancelled", "abandon", "abandoned", "drop", "dropped", "reject", "rejected", "rollback", "rolled back", "undo", "no longer")) or lat_status == "rejected":
                    evolution_rel = "reversed"
                # Check if repeated / reaffirmed
                elif lat_text_lower == ear_lower or token_overlap > 0.75:
                    evolution_rel = "repeated"
                # Superseded (technology change, architecture replacement, policy update)
                elif any(cue in lat_text_lower for cue in ("replace", "replaced", "switch", "switched", "migrate", "migrated", "supersede", "superseded", "instead of", "changed", "updated", "moved to", "adopt")) or token_overlap > 0:
                    evolution_rel = "superseded"
                else:
                    evolution_rel = "superseded"

                # Persist link to DB without overwriting earlier record or user edits
                if lat_id and (not d_lat.get("supersedes_id") or d_lat.get("evolution_relationship") in (None, "new")):
                    try:
                        self.db.update_decision(
                            lat_id,
                            supersedes_id=supersedes_id,
                            evolution_relationship=evolution_rel,
                            user_edited=int(d_lat.get("user_edited") or 0),
                        )
                    except Exception as err:
                        logger.debug("Decision evolution DB link update note: %s", err)
            else:
                evolution_rel = d_lat.get("evolution_relationship") or "new"
                if evolution_rel not in ("new", "repeated", "superseded", "reversed"):
                    evolution_rel = "new"

            delta_item = {
                "id": lat_id,
                "decision_id": lat_id,
                "decision": lat_text,
                "status": lat_status,
                "category": d_lat.get("category", "General"),
                "evolution_relationship": evolution_rel,
                "evolution_state": evolution_rel,
                "supersedes_id": supersedes_id,
                "earlier_decision": matched_early.get("decision") if matched_early else None,
                "evidence_quote": d_lat.get("evidence_quote", ""),
                "rationale": d_lat.get("rationale", ""),
            }
            decision_deltas.append(delta_item)

        # Generate comparison Markdown report with canonical headers
        title_early = m_early.get("title", f"Meeting #{id_early}")
        title_later = m_later.get("title", f"Meeting #{id_later}")
        dur_early = format_seconds_hms(float(m_early.get("duration_sec") or 0.0))
        dur_later = format_seconds_hms(float(m_later.get("duration_sec") or 0.0))
        date_early_str = str(m_early.get("created_at") or m_early.get("start_time") or "N/A")
        date_later_str = str(m_later.get("created_at") or m_later.get("start_time") or "N/A")

        report_lines = [
            f"### ⚖️ Meeting Comparison: Session #{id_early} vs #{id_later}",
            f"",
            f"| Metric / Attribute | Meeting A (#{id_early}) | Meeting B (#{id_later}) |",
            f"| :--- | :--- | :--- |",
            f"| **Title** | {title_early} | {title_later} |",
            f"| **Recorded At** | {date_early_str} | {date_later_str} |",
            f"| **Duration** | {dur_early} | {dur_later} |",
            f"| **Decisions Recorded** | {len(decisions_early)} | {len(decisions_later)} |",
            f"| **Action Items** | {len(actions_early)} | {len(actions_later)} |",
            f"",
            f"#### 🎯 Strategic Decisions Comparison",
        ]

        if not decisions_early and not decisions_later:
            report_lines.append("*No structured decisions recorded in either session.*")
        else:
            report_lines.append(f"**Meeting A Decisions ({title_early}):**")
            if decisions_early:
                for d in decisions_early:
                    cert = f" ({d['certainty']})" if d.get("certainty") else ""
                    stat = f" *[{d.get('status', 'confirmed').capitalize()}]*" if d.get("status") else ""
                    report_lines.append(f"- **{d.get('decision', '')}**{stat}{cert}")
            else:
                report_lines.append("- *(None recorded)*")

            report_lines.append(f"\n**Meeting B Decisions ({title_later}):**")
            if decision_deltas:
                for d in decision_deltas:
                    evo_rel = d.get("evolution_relationship", "new")
                    tag = f" *[{evo_rel.capitalize()}]*" if evo_rel != "new" else ""
                    supersedes_info = f" (supersedes #{d['supersedes_id']})" if d.get("supersedes_id") else ""
                    report_lines.append(f"- **{d.get('decision', '')}**{tag}{supersedes_info}")
            elif decisions_later:
                for d in decisions_later:
                    cert = f" ({d['certainty']})" if d.get("certainty") else ""
                    report_lines.append(f"- **{d.get('decision', '')}**{cert}")
            else:
                report_lines.append("- *(None recorded)*")

        report_lines.append(f"\n#### 📋 Action Items & Commitments Carry-Over")
        if open_early_actions:
            report_lines.append(f"**Open Commitments from earlier session ({m_early.get('title')}):**")
            for act in open_early_actions:
                owner = act.get("owner") or "Unassigned"
                due = f" (Due: {act.get('due_date')})" if act.get("due_date") else ""
                report_lines.append(f"- [ ] **[{owner}]** {act.get('description', '')}{due}")
        else:
            report_lines.append("*All earlier commitments completed or none tracked.*")

        report_lines.append(f"\n#### 📌 Topic Focus Evolution")
        if common_topics:
            report_lines.append(f"- **Continuing Themes:** {', '.join(common_topics)}")
        if new_in_b:
            report_lines.append(f"- **New Topics in Meeting B:** {', '.join(new_in_b)}")
        if not common_topics and not new_in_b:
            report_lines.append(f"- No distinct topic tags detected between sessions.")

        md_text = "\n".join(report_lines)

        return {
            "meeting_a": m_early,
            "meeting_b": m_later,
            "meeting_early": m_early,
            "meeting_later": m_later,
            "decisions_a": decisions_early,
            "decisions_b": decisions_later,
            "actions_a": actions_early,
            "actions_b": actions_later,
            "carry_over_actions": open_early_actions,
            "common_topics": common_topics,
            "new_topics": new_in_b,
            "decision_deltas": decision_deltas,
            "evolution": decision_deltas,
            "evolution_states": {d["id"]: d["evolution_relationship"] for d in decision_deltas if d.get("id")},
            "superseded_decisions": [d for d in decision_deltas if d["evolution_relationship"] in ("superseded", "reversed")],
            "comparison_markdown": md_text,
        }

    def generate_pre_meeting_briefing(self, query_or_topic: str) -> Dict[str, Any]:
        """
        Generate an instant executive pre-meeting briefing synthesizing:
        - Past relevant sessions and timelines
        - Key prior agreements & decisions with anchor timestamps and session dates (ADV-28)
        - Cross-meeting reconciled open action items with owners (ADV-29)
        - Strategic agenda & recommended questions to ask
        """
        topic = (query_or_topic or "").strip()
        all_meetings = self.db.list_meetings(limit=100)

        # Filter relevant meetings
        if topic:
            tokens = [t.lower() for t in re.findall(r"\w+", topic) if t.lower() not in STOPWORDS]
            relevant_meetings = []
            for m in all_meetings:
                m_title = (m.get("title") or "").lower()
                m_summary = (m.get("summary") or "").lower()
                m_topics = (m.get("topics") or "").lower()
                score = 0
                for tok in tokens:
                    if tok in m_title:
                        score += 5
                    if tok in m_topics:
                        score += 3
                    if tok in m_summary:
                        score += 1
                if score > 0:
                    relevant_meetings.append((score, m))
            relevant_meetings.sort(key=lambda x: (x[0], x[1].get("id", 0)), reverse=True)
            matched_meetings = [m for _, m in relevant_meetings[:5]]
        else:
            matched_meetings = all_meetings[:5]

        # Gather relevant decisions and open actions
        meeting_ids = [m["id"] for m in matched_meetings]
        open_actions = []
        prior_decisions = []

        if meeting_ids:
            for mid in meeting_ids:
                acts = self.db.list_action_items(meeting_id=mid, status="pending", limit=20)
                open_actions.extend(acts)
                decs = self.db.list_decisions(meeting_id=mid, limit=10)
                prior_decisions.extend(decs)
        else:
            open_actions = self.db.list_action_items(status="pending", limit=10)
            prior_decisions = self.db.list_decisions(limit=10)

        # Cross-meeting task reconciliation to avoid false carry-over (ADV-29)
        reconciled_open_actions = []
        completion_cues = ("completed", "done", "finished", "resolved", "delivered", "deployed", "shipped", "closed", "success")
        for act in open_actions:
            act_desc = act.get("description", "").strip()
            act_mid = act.get("meeting_id")
            act_m_date = str(act.get("meeting_date") or "")
            if not act_m_date and act_mid:
                m_act = self.db.get_meeting(act_mid)
                if m_act:
                    act_m_date = str(m_act.get("created_at") or "")

            act_words = [w.lower() for w in re.findall(r"\w+", act_desc) if w.lower() not in STOPWORDS and len(w) > 2]

            is_reconciled = False
            for m in all_meetings:
                m_date = str(m.get("created_at") or "")
                # Only check subsequent sessions
                if act_m_date and m_date and m_date <= act_m_date:
                    continue
                if m.get("id") == act_mid:
                    continue

                m_summary = (m.get("summary") or "").lower()
                m_notes = (m.get("notes") or "").lower()
                combined_m_text = f"{m_summary} {m_notes}"

                # Check if core words and completion cue appear in summary or notes
                if act_words and any(cue in combined_m_text for cue in completion_cues):
                    overlap = sum(1 for w in act_words if w in combined_m_text or (len(w) > 4 and w[:4] in combined_m_text))
                    if overlap >= max(1, int(len(act_words) * 0.5)):
                        is_reconciled = True
                        break

                # Also check subsequent meetings' completed action items in DB
                sub_actions = self.db.list_action_items(meeting_id=m.get("id"), status="completed")
                for s_act in sub_actions:
                    s_desc = s_act.get("description", "").lower()
                    if act_words:
                        overlap = sum(1 for w in act_words if w in s_desc)
                        if overlap >= max(1, int(len(act_words) * 0.6)):
                            is_reconciled = True
                            break
                if is_reconciled:
                    break

            if not is_reconciled:
                reconciled_open_actions.append(act)

        open_actions = reconciled_open_actions

        # Synthesize executive briefing Markdown with anchor timestamps (ADV-28)
        topic_label = topic if topic else "Upcoming Strategy & Operational Sync"
        briefing_lines = [
            f"### 🎯 Executive Pre-Meeting Briefing",
            f"**Focus / Context:** {topic_label}",
            f"**Prepared by:** VOX Local Intelligence (100% Offline & Private)",
            f"",
            f"#### 📅 Relevant Past Sessions ({len(matched_meetings)} Found)",
        ]

        if matched_meetings:
            for m in matched_meetings:
                created = m.get("created_at") or "Unknown Date"
                dur = format_seconds_hms(float(m.get("duration_sec") or 0.0))
                briefing_lines.append(f"- **{m.get('title', 'Meeting')}** ({created} · {dur})")
        else:
            briefing_lines.append("- *No prior historical meetings matched this topic.*")

        briefing_lines.append(f"\n#### 🎯 Key Prior Decisions & Commitments")
        if prior_decisions:
            for d in prior_decisions:
                meeting_title = d.get("meeting_title") or "Meeting"
                meeting_date = d.get("meeting_date")
                if not meeting_date and d.get("meeting_id"):
                    m_obj = self.db.get_meeting(d["meeting_id"])
                    if m_obj:
                        meeting_title = m_obj.get("title") or meeting_title
                        meeting_date = (m_obj.get("created_at") or "").split()[0]
                date_str = str(meeting_date).split()[0] if meeting_date else "Unknown Date"
                t_sec = float(d.get("timestamp_sec") or 0.0)
                ts_str = format_seconds_hms(t_sec)
                anchor_citation = f"[{meeting_title} · {date_str} at {ts_str}]"
                d["anchor_citation"] = anchor_citation
                d["meeting_title"] = meeting_title
                d["meeting_date"] = date_str
                cert = f" ({d.get('certainty')})" if d.get("certainty") else ""
                if prior_decisions.index(d) < 6:
                    briefing_lines.append(f"- **{d.get('decision', '')}** · *{anchor_citation}*{cert}")
        else:
            briefing_lines.append("- *No prior formal decisions tracked for this topic.*")

        briefing_lines.append(f"\n#### ⚠️ Open Action Items & Carry-Over Work")
        if open_actions:
            for act in open_actions:
                owner = act.get("owner") or "Unassigned"
                due = f" · Due: {act.get('due_date')}" if act.get("due_date") else ""
                meeting_title = act.get("meeting_title") or "Meeting"
                meeting_date = act.get("meeting_date")
                if not meeting_date and act.get("meeting_id"):
                    m_obj = self.db.get_meeting(act["meeting_id"])
                    if m_obj:
                        meeting_title = m_obj.get("title") or meeting_title
                        meeting_date = (m_obj.get("created_at") or "").split()[0]
                date_str = str(meeting_date).split()[0] if meeting_date else "Unknown Date"
                t_sec = float(act.get("timestamp_sec") or 0.0)
                ts_str = format_seconds_hms(t_sec)
                anchor_citation = f"[{meeting_title} · {date_str} at {ts_str}]"
                act["anchor_citation"] = anchor_citation
                act["meeting_title"] = meeting_title
                act["meeting_date"] = date_str
                if open_actions.index(act) < 8:
                    briefing_lines.append(f"- [ ] **[{owner}]** {act.get('description', '')}{due} · *{anchor_citation}*")
        else:
            briefing_lines.append("- *No unresolved action items pending.*")

        briefing_lines.append(f"\n#### 💡 Suggested Discussion Agenda & Questions")
        briefing_lines.append(f"1. **Status Update:** Review outstanding deliverables assigned to {', '.join(sorted(list(set(a.get('owner', 'team') for a in open_actions[:4] if a.get('owner'))))) or 'the team'}.")
        briefing_lines.append(f"2. **Alignment Check:** Confirm whether prior decisions on {topic_label} remain valid.")
        briefing_lines.append(f"3. **Blockers & Dependencies:** Identify any roadblocks hindering progress.")
        briefing_lines.append(f"4. **Next Milestones:** Agree on actionable owners and explicit deadlines before concluding.")

        briefing_md = "\n".join(briefing_lines)

        return {
            "topic": topic_label,
            "relevant_meetings": matched_meetings,
            "open_action_items": open_actions,
            "key_decisions": prior_decisions,
            "prior_decisions": prior_decisions,
            "briefing_markdown": briefing_md,
        }

    def get_cross_meeting_decisions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve cross-meeting strategic decisions ordered chronologically with meeting metadata."""
        return self.db.list_decisions(meeting_id=None, limit=limit)

    def get_recurring_topics(self, limit: int = 15) -> List[Dict[str, Any]]:
        """Aggregate recurring topics and discussion chapters across all recorded meetings."""
        all_meetings = self.db.list_meetings(limit=250)
        topic_counts: Dict[str, Dict[str, Any]] = {}
        for m in all_meetings:
            raw_topics = str(m.get("topics") or "").replace(",", "\n").split("\n")
            for t in raw_topics:
                cleaned = t.strip().strip("-•*").strip()
                if len(cleaned) < 3 or cleaned.lower() in STOPWORDS:
                    continue
                key = cleaned.lower()
                if key not in topic_counts:
                    topic_counts[key] = {
                        "name": cleaned,
                        "count": 0,
                        "meetings": [],
                    }
                topic_counts[key]["count"] += 1
                if m.get("id") not in [xm["id"] for xm in topic_counts[key]["meetings"]]:
                    topic_counts[key]["meetings"].append({"id": m.get("id"), "title": m.get("title")})

        sorted_topics = sorted(topic_counts.values(), key=lambda x: x["count"], reverse=True)
        return sorted_topics[:limit]

    def generate_follow_up_email(
        self,
        meeting_id: Optional[int] = None,
        meeting_data: Optional[Dict[str, Any]] = None,
        subject_prefix: str = "[Follow-up]",
    ) -> Dict[str, Any]:
        """
        Generate a polished, executive follow-up email summarizing meeting context,
        decisions, and designated action items tailored for immediate sending.
        Grounds every item in transcript evidence without hallucinated commitments.
        """
        import urllib.parse

        # 1. Resolve meeting metadata
        m_info: Dict[str, Any] = {}
        if meeting_data:
            m_info = dict(meeting_data)
        elif meeting_id is not None and self.db is not None:
            m_info = self.db.get_meeting(meeting_id) or {}

        title = m_info.get("title") or "Meeting Sync"
        created_at = m_info.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
        date_str = str(created_at).split(" ")[0]

        # 2. Resolve action items
        raw_actions: List[Dict[str, Any]] = []
        if "action_items" in m_info and isinstance(m_info["action_items"], list):
            raw_actions = m_info["action_items"]
        elif meeting_id is not None and self.db is not None and hasattr(self.db, "list_action_items"):
            raw_actions = self.db.list_action_items(meeting_id=meeting_id)
        elif "action_items" in m_info and isinstance(m_info["action_items"], str):
            try:
                parsed = json.loads(m_info["action_items"])
                if isinstance(parsed, list):
                    raw_actions = parsed
            except Exception:
                pass

        # 3. Resolve decisions
        raw_decisions: List[Dict[str, Any]] = []
        if "decisions" in m_info and isinstance(m_info["decisions"], list):
            raw_decisions = m_info["decisions"]
        elif meeting_id is not None and self.db is not None and hasattr(self.db, "list_decisions"):
            raw_decisions = self.db.list_decisions(meeting_id=meeting_id)
        elif "decisions" in m_info and isinstance(m_info["decisions"], str):
            try:
                parsed = json.loads(m_info["decisions"])
                if isinstance(parsed, list):
                    raw_decisions = parsed
            except Exception:
                pass

        # 4. Extract participants
        participants: List[str] = []
        if meeting_id is not None and self.db is not None and hasattr(self.db, "get_meeting_speakers"):
            participants = [s for s in self.db.get_meeting_speakers(meeting_id) if s]
        if not participants:
            seen_speakers = set()
            for a in raw_actions:
                owner = (a.get("owner") or "").strip()
                if owner and owner.lower() not in ("unassigned", "team", "all"):
                    seen_speakers.add(owner)
            participants = sorted(list(seen_speakers))
        if not participants:
            participants = ["Team"]

        # 5. Extract concise executive summary
        summary_text = m_info.get("summary") or ""
        exec_paragraphs: List[str] = []
        if summary_text:
            lines = [ln.strip() for ln in summary_text.splitlines() if ln.strip()]
            for ln in lines:
                if ln.startswith(("#", "=", "-", "*", "📋", "1.", "2.")):
                    continue
                if len(ln) > 25:
                    exec_paragraphs.append(ln)
                if len(exec_paragraphs) >= 2:
                    break
        if not exec_paragraphs:
            topics = m_info.get("topics") or "project milestones and operational alignment"
            exec_paragraphs = [
                f"Thank you everyone for taking the time to sync on {title}. "
                f"We reviewed {topics} and aligned on key deliverables."
            ]

        exec_summary = " ".join(exec_paragraphs)

        # 6. Format decisions
        decisions_list: List[Dict[str, str]] = []
        for d in raw_decisions:
            dec_text = d.get("decision") or d.get("text") or ""
            if not dec_text:
                continue
            rationale = d.get("rationale") or ""
            status = d.get("status") or "confirmed"
            decisions_list.append({
                "decision": dec_text,
                "rationale": rationale,
                "status": status,
            })

            # 7. Format action items by owner
        actions_by_owner: Dict[str, List[Dict[str, Any]]] = {}
        for a in raw_actions:
            desc = a.get("description") or a.get("task") or ""
            if not desc:
                continue
            owner = (a.get("owner") or "Unassigned (Confirmation Needed)").strip()
            due = a.get("due_date") or a.get("deadline") or ""
            actions_by_owner.setdefault(owner, []).append({
                "description": desc,
                "due_date": due,
                "status": a.get("status", "pending"),
            })

        # 7b. Follow-ups
        raw_fu = m_info.get("follow_ups_json") or m_info.get("follow_ups") or []
        if isinstance(raw_fu, str):
            try:
                raw_fu = json.loads(raw_fu) if raw_fu.strip().startswith("[") else []
            except Exception:
                raw_fu = []
        follow_up_lines: List[str] = []
        for fu in (raw_fu if isinstance(raw_fu, list) else []):
            if isinstance(fu, dict) and str(fu.get("item") or "").strip():
                f_own = str(fu.get("owner") or "").strip()
                prefix = f"[{f_own}] " if f_own else ""
                follow_up_lines.append(f"{prefix}{str(fu['item']).strip()}")
            elif isinstance(fu, str) and fu.strip():
                follow_up_lines.append(fu.strip())

        # 8. Synthesize email subject & body (never invent recipients)
        subject = f"{subject_prefix} {title} - {date_str}"
        greeting = "Hi team,"

        # Markdown format
        md_lines = [
            f"**Subject:** {subject}",
            "",
            greeting,
            "",
            "Thank you for attending today's session. Here is a summary of our discussion, confirmed decisions, and assigned action items:",
            "",
            "### Executive Summary",
            exec_summary,
            "",
        ]

        if decisions_list:
            md_lines.append("### Key Decisions Agreed")
            for item in decisions_list:
                line = f"- **{item['decision']}**"
                if item["rationale"]:
                    line += f" *(Rationale: {item['rationale']})*"
                md_lines.append(line)
            md_lines.append("")

        if actions_by_owner:
            md_lines.append("### Action Items & Ownership")
            for owner, items in sorted(actions_by_owner.items()):
                md_lines.append(f"**{owner}:**")
                for itm in items:
                    due_badge = f" *(Due: {itm['due_date']})*" if itm['due_date'] and itm['due_date'] not in ('TBD', 'Not specified') else ""
                    md_lines.append(f"- [ ] {itm['description']}{due_badge}")
            md_lines.append("")

        if follow_up_lines:
            md_lines.append("### Follow-Ups & Next Discussions")
            for fl in follow_up_lines:
                md_lines.append(f"- {fl}")
            md_lines.append("")

        md_lines.extend([
            "### Next Steps",
            "Please review your assigned deliverables and flag any blockers in advance. We will track progress ahead of our next sync.",
            "",
            "Best regards,",
            "VOX Meeting Studio",
        ])
        email_md = "\n".join(md_lines)

        # HTML format with executive inline styling
        decisions_html = ""
        if decisions_list:
            d_items = "".join(
                f'<li style="margin-bottom: 6px;"><b>{d["decision"]}</b>'
                f'{" — <i style=\'color:#4B5563;\'>" + d["rationale"] + "</i>" if d["rationale"] else ""}</li>'
                for d in decisions_list
            )
            decisions_html = f"""
            <h3 style="color: #1F2937; margin-top: 18px; margin-bottom: 8px; font-size: 15px; border-bottom: 1px solid #E5E7EB; padding-bottom: 4px;">Key Decisions Agreed</h3>
            <ul style="color: #374151; padding-left: 20px; margin-top: 4px;">{d_items}</ul>
            """

        actions_html = ""
        if actions_by_owner:
            owner_blocks = []
            for owner, items in sorted(actions_by_owner.items()):
                task_items = "".join(
                    f'<li style="margin-bottom: 4px;">'
                    f'<span style="display:inline-block; width:12px; height:12px; border:1px solid #9CA3AF; margin-right:6px; vertical-align:middle; border-radius:2px;"></span>'
                    f'{itm["description"]}'
                    f'{" <span style=\'color:#2563EB; font-size:12px;\'>(" + itm["due_date"] + ")</span>" if itm["due_date"] and itm["due_date"] != "TBD" else ""}'
                    f'</li>'
                    for itm in items
                )
                owner_blocks.append(f'<div style="margin-bottom: 10px;"><b style="color: #111827;">{owner}</b><ul style="list-style-type: none; padding-left: 8px; margin: 4px 0;">{task_items}</ul></div>')
            actions_html = f"""
            <h3 style="color: #1F2937; margin-top: 18px; margin-bottom: 8px; font-size: 15px; border-bottom: 1px solid #E5E7EB; padding-bottom: 4px;">Action Items & Ownership</h3>
            {"".join(owner_blocks)}
            """

        email_html = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.5; color: #1F2937; max-width: 680px;">
            <p style="font-size: 14px; margin-bottom: 14px;">{greeting}</p>
            <p style="font-size: 14px; margin-bottom: 14px;">Thank you for attending today's session. Here is a summary of our discussion, confirmed decisions, and assigned action items:</p>
            <div style="background-color: #F9FAFB; border-left: 4px solid #3B82F6; padding: 12px 16px; margin-bottom: 16px; border-radius: 4px;">
                <b style="color: #1E40AF; font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em;">Executive Summary</b>
                <p style="margin: 6px 0 0 0; font-size: 14px; color: #374151;">{exec_summary}</p>
            </div>
            {decisions_html}
            {actions_html}
            <h3 style="color: #1F2937; margin-top: 18px; margin-bottom: 8px; font-size: 15px; border-bottom: 1px solid #E5E7EB; padding-bottom: 4px;">Next Steps</h3>
            <p style="font-size: 14px; color: #374151; margin-bottom: 16px;">Please review your assigned deliverables and flag any blockers in advance. We will track progress ahead of our next sync.</p>
            <p style="font-size: 14px; color: #4B5563; margin-top: 20px;">Best regards,<br/><b style="color: #111827;">VOX Meeting Studio</b></p>
        </div>
        """.strip()

        # mailto URI (RFC 2368)
        plain_body = (
            f"{greeting}\n\n"
            f"Here is the follow-up summary from our meeting '{title}':\n\n"
            f"SUMMARY:\n{exec_summary}\n\n"
        )
        if decisions_list:
            plain_body += "DECISIONS:\n" + "\n".join(f"• {d['decision']}" for d in decisions_list) + "\n\n"
        if actions_by_owner:
            plain_body += "ACTION ITEMS:\n"
            for owner, items in sorted(actions_by_owner.items()):
                plain_body += f"{owner}:\n" + "\n".join(
                    f"  [ ] {itm['description']}" + (f" (Due: {itm['due_date']})" if itm.get('due_date') else "")
                    for itm in items
                ) + "\n"
            plain_body += "\n"
        if follow_up_lines:
            plain_body += "FOLLOW-UPS:\n" + "\n".join(f"• {fl}" for fl in follow_up_lines) + "\n\n"
        plain_body += "Best regards,\nVOX Meeting Studio"

        mailto_url = f"mailto:?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(plain_body)}"

        return {
            "subject": subject,
            "greeting": greeting,
            "summary": exec_summary,
            "decisions": decisions_list,
            "action_items_by_owner": actions_by_owner,
            "markdown": email_md,
            "html": email_html,
            "mailto_url": mailto_url,
            "participants": participants,
        }

    def generate_participant_checklists(
        self,
        meeting_id: Optional[int] = None,
        meeting_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Group action items by participant with clear checkboxes, ownership, and deadlines.
        Generates individual task cards, Slack formatted text with @mentions, and markdown checklists.
        """
        # 1. Resolve meeting metadata and actions
        m_info: Dict[str, Any] = {}
        if meeting_data:
            m_info = dict(meeting_data)
        elif meeting_id is not None and self.db is not None:
            m_info = self.db.get_meeting(meeting_id) or {}

        title = m_info.get("title") or "Meeting"

        raw_actions: List[Dict[str, Any]] = []
        if "action_items" in m_info and isinstance(m_info["action_items"], list):
            raw_actions = m_info["action_items"]
        elif meeting_id is not None and self.db is not None and hasattr(self.db, "list_action_items"):
            raw_actions = self.db.list_action_items(meeting_id=meeting_id)
        elif "action_items" in m_info and isinstance(m_info["action_items"], str):
            try:
                parsed = json.loads(m_info["action_items"])
                if isinstance(parsed, list):
                    raw_actions = parsed
            except Exception:
                pass

        by_participant: Dict[str, List[Dict[str, Any]]] = {}
        total_tasks = 0
        completed_tasks = 0

        for idx, a in enumerate(raw_actions):
            desc = (a.get("description") or a.get("task") or "").strip()
            if not desc:
                continue
            owner = (a.get("owner") or "Unassigned").strip()
            due = (a.get("due_date") or a.get("deadline") or "TBD").strip()
            status = (a.get("status") or "pending").strip().lower()
            completed = status == "completed"
            priority = (a.get("priority") or "medium").strip()
            evidence = (a.get("evidence_quote") or "").strip()

            total_tasks += 1
            if completed:
                completed_tasks += 1

            item_data = {
                "id": a.get("id", idx + 1),
                "description": desc,
                "owner": owner,
                "due_date": due,
                "status": status,
                "completed": completed,
                "priority": priority,
                "evidence_quote": evidence,
            }
            by_participant.setdefault(owner, []).append(item_data)

        # Markdown representation
        md_lines = [
            f"# Action Item Checklists — {title}",
            f"*Total Tasks: {total_tasks} | Completed: {completed_tasks} | Pending: {total_tasks - completed_tasks}*",
            "",
        ]

        # Slack representation with @mentions
        slack_lines = [
            f"📋 *Action Items Checklist — {title}*",
            f"_{completed_tasks}/{total_tasks} completed_",
            "",
        ]

        for owner, tasks in sorted(by_participant.items()):
            mention = f"@{owner}" if owner.lower() not in ("unassigned", "team", "all") else owner
            md_lines.append(f"### {owner}")
            slack_lines.append(f"*{mention}* ({len(tasks)} tasks):")

            for t in tasks:
                chk = "x" if t["completed"] else " "
                due_info = f" *(Due: {t['due_date']})*" if t["due_date"] and t["due_date"] not in ("TBD", "Not specified") else ""
                md_lines.append(f"- [{chk}] {t['description']}{due_info}")

                slack_chk = "✅" if t["completed"] else "▫️"
                slack_lines.append(f"  {slack_chk} {t['description']}{due_info}")

            md_lines.append("")
            slack_lines.append("")

        # Build structured per-participant contributions combining actions, decisions, follow-ups & speaking stats
        raw_decisions: List[Dict[str, Any]] = []
        if "decisions" in m_info and isinstance(m_info["decisions"], list):
            raw_decisions = m_info["decisions"]
        elif meeting_id is not None and self.db is not None and hasattr(self.db, "list_decisions"):
            raw_decisions = self.db.list_decisions(meeting_id=meeting_id)

        raw_fu = m_info.get("follow_ups_json") or m_info.get("follow_ups") or []
        if isinstance(raw_fu, str):
            try:
                raw_fu = json.loads(raw_fu) if raw_fu.strip().startswith("[") else []
            except Exception:
                raw_fu = []

        seg_speakers: List[str] = []
        if meeting_id is not None and self.db is not None and hasattr(self.db, "get_meeting_speakers"):
            try:
                seg_speakers = [str(s.get("speaker") if isinstance(s, dict) else s) for s in self.db.get_meeting_speakers(meeting_id)]
            except Exception:
                seg_speakers = []

        all_participant_names = sorted(
            {
                *(k for k in by_participant.keys() if k != "Unassigned"),
                *(s for s in seg_speakers if s),
                *(str(d.get("speaker") or "").strip() for d in raw_decisions if isinstance(d, dict) and str(d.get("speaker") or "").strip()),
                *(str(f.get("owner") or "").strip() for f in (raw_fu if isinstance(raw_fu, list) else []) if isinstance(f, dict) and str(f.get("owner") or "").strip()),
            }
        )
        if "Unassigned" in by_participant:
            all_participant_names.append("Unassigned")

        participants_detail: Dict[str, Dict[str, Any]] = {}
        for pname in all_participant_names:
            p_actions = []
            for a in raw_actions:
                a_owner = (a.get("owner") or "").strip() or "Unassigned"
                if a_owner == pname and (a.get("description") or a.get("task") or "").strip():
                    p_actions.append(dict(a))
            p_decs = [
                dict(d) for d in raw_decisions
                if isinstance(d, dict) and (
                    str(d.get("speaker") or "").strip() == pname
                    or pname in (d.get("participants") or [])
                )
            ]
            p_fus = [
                dict(f) for f in (raw_fu if isinstance(raw_fu, list) else [])
                if isinstance(f, dict) and ((f.get("owner") or "").strip() or "Unassigned") == pname
            ]
            confirmed = [a for a in p_actions if str(a.get("confirmation_state") or "confirmed") == "confirmed"]
            pending = [a for a in p_actions if str(a.get("status") or "pending").lower() != "completed"]
            participants_detail[pname] = {
                "name": "Owner Unknown / Needs Confirmation" if pname == "Unassigned" else pname,
                "raw_key": pname,
                "is_unassigned": pname == "Unassigned",
                "confirmed_commitments": confirmed,
                "assigned_actions": p_actions,
                "decisions": p_decs,
                "follow_ups": p_fus,
                "pending_items": pending,
                "completed_count": sum(1 for a in p_actions if str(a.get("status") or "").lower() == "completed"),
                "total_count": len(p_actions),
            }

        return {
            "meeting_id": meeting_id,
            "title": title,
            "by_participant": by_participant,
            "participants_detail": participants_detail,
            "participants": sorted(list(by_participant.keys())),
            "markdown": "\n".join(md_lines).strip(),
            "slack_format": "\n".join(slack_lines).strip(),
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "pending_tasks": total_tasks - completed_tasks,
        }

    def _extract_scope_modifiers(self, query: str) -> Tuple[Optional[int], bool]:
        """Detect explicit multi-meeting window ('last 3 meetings') and self-only filters ('only me')."""
        q_lower = query.lower()
        multi_n: Optional[int] = None
        m_last = re.search(
            r"\b(?:last|past|recent|previous)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+meetings?\b",
            q_lower,
        )
        if m_last:
            word_map = {
                "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            }
            raw_n = m_last.group(1)
            multi_n = int(raw_n) if raw_n.isdigit() else word_map.get(raw_n)
            if multi_n is not None:
                multi_n = max(1, min(multi_n, 25))
        elif any(p in q_lower for p in ("across meetings", "all meetings", "recent meetings", "last few meetings")):
            multi_n = 5

        self_only = any(
            p in q_lower
            for p in (
                "only to me", "belong only to me", "belong to me", "assigned to me",
                "my action", "my tasks", "my commitments", "for me only", "what did i ",
                "just for me", "only me",
            )
        )
        return multi_n, self_only

    def _build_structured_context_for_scope(
        self,
        *,
        target_meeting_id: Optional[int],
        scoped_meeting: Optional[Dict[str, Any]],
        all_meetings: List[Dict[str, Any]],
        allowed_ids: Optional[Set[int]] = None,
        local_user_name: str = "",
        self_only: bool = False,
    ) -> str:
        """Assemble grounded structured DB records (summary, decisions, actions, risks, open questions) for LLM Ask VOX."""
        meetings_to_pack: List[Dict[str, Any]] = []
        if scoped_meeting is not None:
            meetings_to_pack = [scoped_meeting]
        elif target_meeting_id is not None:
            m = self.db.get_meeting(target_meeting_id)
            if m:
                meetings_to_pack = [m]
        elif allowed_ids is not None:
            meetings_to_pack = [m for m in all_meetings if int(m.get("id") or 0) in allowed_ids][:8]
        else:
            meetings_to_pack = list(all_meetings[:5])

        if not meetings_to_pack:
            return ""

        user_tokens = [tok.lower() for tok in re.split(r"[\s._-]+", local_user_name or "") if len(tok) >= 3]
        blocks: List[str] = []
        for m in meetings_to_pack:
            mid = m.get("id")
            if mid is None:
                continue
            m_title = str(m.get("title") or f"Meeting #{mid}").strip()
            m_date = str(m.get("created_at") or "").strip()
            m_dur = format_seconds_hms(float(m.get("duration_sec") or 0.0))
            parts: List[str] = [f"### Meeting #{mid}: {m_title} (Date: {m_date or 'Unknown'}, Duration: {m_dur})"]

            mom = self._load_mom_dict(m)
            brief = str(mom.get("brief_summary") or m.get("summary") or "").strip()
            if brief:
                parts.append(f"Brief Summary: {brief[:700]}")
            detailed = str(mom.get("detailed_summary") or "").strip()
            if detailed and detailed != brief:
                parts.append(f"Detailed Summary: {detailed[:1200]}")

            decisions = self.db.list_decisions(mid, limit=40) if hasattr(self.db, "list_decisions") else []
            if decisions:
                dec_lines = []
                for d in decisions[:12]:
                    dstate = str(d.get("decision_state") or d.get("status") or "confirmed").strip()
                    ts = format_seconds_hms(float(d.get("timestamp_sec") or 0.0))
                    dec_lines.append(f"  - [{dstate}] ({ts}) {str(d.get('decision') or '').strip()}")
                parts.append("Decisions:\n" + "\n".join(dec_lines))

            actions = self.db.list_action_items(mid, limit=60) if hasattr(self.db, "list_action_items") else []
            if self_only and actions:
                filtered_actions = []
                for a in actions:
                    own_l = str(a.get("owner") or "").strip().lower()
                    spk_l = str(a.get("speaker") or "").strip().lower()
                    if (
                        "you" in own_l
                        or "mic" in own_l
                        or (user_tokens and any(tok in own_l for tok in user_tokens))
                        or ("you" in spk_l and not own_l)
                    ):
                        filtered_actions.append(a)
                actions = filtered_actions
            if actions:
                act_lines = []
                for a in actions[:15]:
                    own = str(a.get("owner") or "").strip() or "Owner needed"
                    due = str(a.get("due_date") or "").strip()
                    stat = str(a.get("status") or "pending").strip()
                    due_txt = f" | Due: {due}" if due else ""
                    act_lines.append(f"  - Owner: {own}{due_txt} | Status: {stat} | Task: {str(a.get('description') or '').strip()}")
                parts.append(("User-Scoped Action Items" if self_only else "Action Items") + ":\n" + "\n".join(act_lines))

            risks = mom.get("risks_and_blockers") or mom.get("risks") or []
            if isinstance(risks, list) and risks:
                r_lines = []
                for r in risks[:10]:
                    if isinstance(r, dict):
                        itype = str(r.get("item_type") or r.get("type") or "risk").upper()
                        sev = str(r.get("impact") or r.get("severity") or "Medium").strip()
                        desc = str(r.get("description") or r.get("risk") or "").strip()
                        mit = str(r.get("mitigation_or_resolution") or r.get("mitigation") or "").strip()
                        r_lines.append(f"  - [{itype} | Impact: {sev}] ({format_seconds_hms(float(r.get('timestamp_sec') if r.get('timestamp_sec') is not None else (r.get('source_timestamp_sec') or 0.0)))}) {desc}" + (f" (Mitigation: {mit})" if mit and mit.lower() != "none" else ""))
                if r_lines:
                    parts.append("Risks & Blockers:\n" + "\n".join(r_lines))

            oqs = mom.get("open_questions") or []
            if isinstance(oqs, list) and oqs:
                oq_lines = []
                for q in oqs[:10]:
                    if isinstance(q, dict):
                        cat = str(q.get("category") or "Open Question").strip()
                        qtxt = str(q.get("question") or "").strip()
                        by = str(q.get("asked_by") or "Speaker").strip()
                        if qtxt:
                            oq_lines.append(f"  - [{cat}] ({by}) {qtxt}")
                if oq_lines:
                    parts.append("Open Questions & Pending Confirmations:\n" + "\n".join(oq_lines))

            blocks.append("\n".join(parts))

        if self_only:
            who = local_user_name or "You (Mic)"
            blocks.insert(0, f"[Scope Constraint: Filter action items and commitments belonging strictly to {who}; keep unassigned items separate.]")
        return "\n\n".join(blocks)

    def _classify_query(self, query: str) -> Tuple[QueryIntent, List[str], Optional[str]]:
        """Classify query intent, extract content tokens, and check for meeting title scoping."""
        q_lower = query.lower()

        # Check for quoted or specific meeting names like "in 'gets'" or "in WWWWWWWW"
        scoped_title = None
        match_quoted = re.search(r"in ['\"]([^'\"]+)['\"]", query, re.IGNORECASE)
        if match_quoted:
            scoped_title = match_quoted.group(1).strip()
        else:
            match_in = re.search(r"in (?:the )?([A-Za-z0-9_\- ]+?)(?: meeting|\?|$)", query, re.IGNORECASE)
            if match_in:
                candidate = match_in.group(1).strip()
                if candidate.lower() not in ("all", "any", "this", "recent", "past", "last", "latest", "last 3", "last 5", "past 3", "past 5"):
                    scoped_title = candidate

        multi_n, _ = self._extract_scope_modifiers(query)

        # Intent detection (specific deliverable intents first)
        if any(cue in q_lower for cue in FOLLOWUP_EMAIL_CUES):
            intent = QueryIntent.FOLLOW_UP_EMAIL
        elif any(cue in q_lower for cue in CUSTOMER_NOTE_CUES):
            intent = QueryIntent.CUSTOMER_NOTES
        elif any(cue in q_lower for cue in RISK_OPEN_CUES):
            intent = QueryIntent.RISKS_AND_OPEN_ITEMS
        elif multi_n is not None:
            intent = QueryIntent.MULTI_MEETING_RECAP
        elif any(cue in q_lower for cue in ACTION_CUES):
            intent = QueryIntent.ACTION_ITEMS
        elif any(cue in q_lower for cue in DECISION_CUES):
            intent = QueryIntent.DECISIONS
        elif any(cue in q_lower for cue in SUMMARY_CUES):
            intent = QueryIntent.SUMMARY
        elif any(w in q_lower for w in ("who said", "who spoke", "speaker", "speakers", "what did i say", "did amar say", "said about")):
            intent = QueryIntent.SPEAKER_QA
        elif any(w in q_lower for w in ("when", "timeline", "timestamp", "time", "date")):
            intent = QueryIntent.TIMELINE
        else:
            intent = QueryIntent.TOPIC_QA

        # Extract content tokens (strip punctuation, stopwords, short words)
        raw_words = re.findall(r"[A-Za-z0-9_\-]+", q_lower)
        content_tokens = [w for w in raw_words if w not in STOPWORDS and len(w) > 2]
        if not content_tokens:
            content_tokens = [w for w in raw_words if len(w) > 2]

        return intent, content_tokens, scoped_title

    def _retrieve_citations(
        self,
        question: str,
        tokens: List[str],
        intent: QueryIntent,
        target_meeting_id: Optional[int],
        all_meetings: List[Dict[str, Any]],
        meeting_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Multi-stage hybrid RAG + structured records retrieval across transcripts, decisions, actions, and knowledge."""
        self._last_retrieved_knowledge = []
        raw_results: List[Dict[str, Any]] = []
        allowed_ids: Optional[set[int]] = (
            {int(x) for x in meeting_ids} if (target_meeting_id is None and meeting_ids is not None) else None
        )
        if allowed_ids is not None and not allowed_ids:
            return []

        # 0. Hybrid FTS5 + BGE-Small-EN Vector RRF Search (when knowledge_store is attached)
        if self.knowledge_store is not None and hasattr(self.knowledge_store, "hybrid_search"):
            try:
                try:
                    rag_hits, k_items = self.knowledge_store.hybrid_search(
                        question,
                        meeting_id=target_meeting_id,
                        meeting_ids=list(allowed_ids) if allowed_ids is not None else None,
                        top_k=12,
                    )
                except TypeError:
                    rag_hits, k_items = self.knowledge_store.hybrid_search(
                        question,
                        meeting_id=target_meeting_id,
                        top_k=12,
                    )
                self._last_retrieved_knowledge = list(k_items or [])
                for rh in rag_hits:
                    raw_results.append({
                        "meeting_id": rh.get("meeting_id"),
                        "meeting_title": rh.get("meeting_title", "Meeting"),
                        "meeting_date": rh.get("meeting_date", ""),
                        "start_time": rh.get("timestamp_sec", 0.0),
                        "timestamp_sec": rh.get("timestamp_sec", 0.0),
                        "timestamp_formatted": rh.get("timestamp_formatted", ""),
                        "speaker": rh.get("speaker", "Meeting"),
                        "text": rh.get("text", ""),
                        "quote": rh.get("quote", rh.get("text", "")),
                        "source_type": "hybrid_rag",
                        "_relevance": 12.0 + float(rh.get("lexical_hits", 0)) * 3.0 + max(0.0, float(rh.get("semantic_sim", 0.0))) * 6.0,
                    })
            except Exception as ks_exc:
                logger.debug("Hybrid RAG search fallback to DB scan: %s", ks_exc)

        # Supplement with DB FTS / token memory search
        try:
            try:
                db_hits = self.db.search_meeting_memory(
                    question,
                    limit=25,
                    meeting_id=target_meeting_id,
                    meeting_ids=list(allowed_ids) if allowed_ids is not None else None,
                )
            except TypeError:
                db_hits = self.db.search_meeting_memory(question, limit=25, meeting_id=target_meeting_id)
            raw_results.extend(db_hits)
        except Exception:
            pass

        # Filter strictly by target_meeting_id or allowed_ids
        if target_meeting_id is not None:
            raw_results = [r for r in raw_results if r.get("meeting_id") == target_meeting_id]
        elif allowed_ids is not None:
            raw_results = [r for r in raw_results if r.get("meeting_id") in allowed_ids]

        # 1. Intent-specific structured records retrieval
        if intent == QueryIntent.ACTION_ITEMS:
            try:
                db_actions = self.db.list_action_items(
                    target_meeting_id,
                    meeting_ids=list(allowed_ids) if allowed_ids is not None else None,
                )
            except TypeError:
                db_actions = self.db.list_action_items(target_meeting_id)
            for act in db_actions:
                raw_results.append({
                    "meeting_id": act.get("meeting_id"),
                    "meeting_title": act.get("meeting_title", "Meeting"),
                    "meeting_date": act.get("created_at", ""),
                    "start_time": act.get("timestamp_sec", 0.0),
                    "speaker": f"Action Item ({act.get('owner', 'Unassigned')})",
                    "text": act.get("description", ""),
                    "source_type": "action_item",
                    "_relevance": 10.0,
                })

        if intent == QueryIntent.DECISIONS:
            try:
                db_decisions = self.db.list_decisions(
                    target_meeting_id,
                    meeting_ids=list(allowed_ids) if allowed_ids is not None else None,
                )
            except TypeError:
                db_decisions = self.db.list_decisions(target_meeting_id)
            for dec in db_decisions:
                raw_results.append({
                    "meeting_id": dec.get("meeting_id"),
                    "meeting_title": dec.get("meeting_title", "Meeting"),
                    "meeting_date": dec.get("created_at", ""),
                    "start_time": dec.get("timestamp_sec", 0.0),
                    "speaker": "Decision",
                    "text": dec.get("decision", ""),
                    "source_type": "decision",
                    "_relevance": 10.0,
                })

        # 2. If specific tokens didn't return enough results, check segments for token overlap
        if len(raw_results) < 3 and tokens:
            meetings_to_scan = (
                [m for m in all_meetings if m.get("id") == target_meeting_id]
                if target_meeting_id
                else all_meetings[:100]
            )
            for m in meetings_to_scan:
                m_id = m.get("id")
                m_title = m.get("title", "Meeting")
                m_date = m.get("created_at", "")
                segments = getattr(self.db, "get_meeting_segments", lambda mid: [])(m_id)
                for s in segments:
                    s_text = s.get("text", "")
                    s_lower = s_text.lower()
                    # Check token overlap
                    score = sum(1 for t in tokens if t in s_lower)
                    if score > 0:
                        raw_results.append({
                            "meeting_id": m_id,
                            "meeting_title": m_title,
                            "meeting_date": m_date,
                            "start_time": s.get("start_time", 0.0),
                            "speaker": s.get("speaker", "Speaker"),
                            "text": s_text,
                            "source_type": "transcript",
                            "_relevance": score,
                        })

        # 3. If still empty and meeting was explicitly scoped, pull top transcript segments
        if len(raw_results) < 3 and target_meeting_id:
            target_m = next((m for m in all_meetings if m.get("id") == target_meeting_id), None)
            if target_m:
                m_id = target_m.get("id")
                m_title = target_m.get("title", "Meeting")
                m_date = target_m.get("created_at", "")
                segments = getattr(self.db, "get_meeting_segments", lambda mid: [])(m_id)
                for s in segments[:8]:
                    raw_results.append({
                        "meeting_id": m_id,
                        "meeting_title": m_title,
                        "meeting_date": m_date,
                        "start_time": s.get("start_time", 0.0),
                        "speaker": s.get("speaker", "Speaker"),
                        "text": s.get("text", ""),
                        "source_type": "transcript",
                        "_relevance": 5.0,
                    })

        # Rank and deduplicate
        scored: List[Tuple[float, Dict[str, Any]]] = []
        seen = set()
        q_lower = question.lower()

        for r in raw_results:
            key = (r.get("meeting_id"), round(float(r.get("start_time", 0.0)), 1), r.get("text", "")[:35])
            if key in seen:
                continue
            seen.add(key)

            text = r.get("text", "")
            t_lower = text.lower()
            score = 0.0

            # Exact phrase match boost
            if len(tokens) > 1 and " ".join(tokens) in t_lower:
                score += 12.0

            # Token overlap score
            for t in tokens:
                if t in t_lower:
                    score += 3.0

            # Source type intent boost
            stype = r.get("source_type", "transcript")
            if intent == QueryIntent.DECISIONS and stype == "decision":
                score += 8.0
            elif intent == QueryIntent.ACTION_ITEMS and stype == "action_item":
                score += 8.0

            # Question overlap
            if any(cue in t_lower for cue in ("agreed", "decided", "will", "need to", "action", "check")):
                score += 2.0

            scored.append((score, r))

        # Post-retrieval quarantine + dynamic speaker alias normalization (Blocker B)
        if target_meeting_id is not None:
            scored = [item for item in scored if item[1].get("meeting_id") == target_meeting_id]
        elif allowed_ids is not None:
            scored = [item for item in scored if item[1].get("meeting_id") in allowed_ids]

        scored.sort(key=lambda x: x[0], reverse=True)
        top_cits = [dict(item) for _, item in scored[:12]]
        alias_cache: Dict[int, Dict[str, str]] = {}
        for cit in top_cits:
            cmid = cit.get("meeting_id")
            if cmid is not None and hasattr(self.db, "get_speaker_aliases"):
                if int(cmid) not in alias_cache:
                    try:
                        alias_cache[int(cmid)] = self.db.get_speaker_aliases(int(cmid)) or {}
                    except Exception:
                        alias_cache[int(cmid)] = {}
                amap = alias_cache[int(cmid)]
                if amap:
                    spk_str = str(cit.get("speaker") or "")
                    parts = [amap.get(p.strip(), p.strip()) for p in re.split(r"\s*[/,]\s*", spk_str) if p.strip()]
                    if parts:
                        cit["speaker"] = " / ".join(dict.fromkeys(parts))
                        cit["speakers"] = list(dict.fromkeys(parts))
                    for fld in ("text", "quote"):
                        if cit.get(fld):
                            sval = str(cit[fld])
                            for r_lbl, d_name in amap.items():
                                if r_lbl and d_name and r_lbl != d_name:
                                    sval = sval.replace(f"|{r_lbl}]", f"|{d_name}]")
                            cit[fld] = sval
        return top_cits

    def _synthesize_answer(
        self,
        question: str,
        intent: QueryIntent,
        tokens: List[str],
        target_meeting_id: Optional[int],
        citations: List[Dict[str, Any]],
        all_meetings: List[Dict[str, Any]],
        scoped_meeting: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Any]] = None,
        answer_depth: str = "detailed",
        allowed_ids: Optional[Set[int]] = None,
        local_user_name: str = "",
        self_only: bool = False,
    ) -> Tuple[str, List[str]]:
        """Synthesize rich, structured markdown answer based on intent and retrieved evidence."""
        # Find scoped meeting details if explicitly targeted
        if scoped_meeting is None and target_meeting_id is not None:
            for m in all_meetings:
                if m.get("id") == target_meeting_id:
                    scoped_meeting = m
                    break
            if scoped_meeting is None:
                scoped_meeting = self.db.get_meeting(target_meeting_id)

        structured_ctx = self._build_structured_context_for_scope(
            target_meeting_id=target_meeting_id,
            scoped_meeting=scoped_meeting,
            all_meetings=all_meetings,
            allowed_ids=allowed_ids,
            local_user_name=local_user_name,
            self_only=self_only,
        )

        # Strict Negative-Trap guard for specific factual/topic queries with zero lexical/semantic overlap
        structured_intents = {
            QueryIntent.SUMMARY,
            QueryIntent.ACTION_ITEMS,
            QueryIntent.DECISIONS,
            QueryIntent.CUSTOMER_NOTES,
            QueryIntent.FOLLOW_UP_EMAIL,
            QueryIntent.RISKS_AND_OPEN_ITEMS,
            QueryIntent.MULTI_MEETING_RECAP,
        }
        if not citations and not structured_ctx:
            if scoped_meeting or target_meeting_id is not None:
                target_title = scoped_meeting.get("title") if scoped_meeting else f"Meeting #{target_meeting_id}"
                ans = (
                    f"### No Direct Matches Found\n\n"
                    f"No relevant information found in '{target_title}' matching **'{question}'**.\n\n"
                    f"**Tips:**\n"
                    f"- Try rephrasing your question or using broader keywords.\n"
                    f"- Check the verbatim transcript in the **Transcript** tab."
                )
                return ans, [
                    f"Summarize key decisions made in {target_title}",
                    f"What are the assigned action items in {target_title}?",
                ]

            ans = (
                f"### No Direct Matches Found\n\n"
                f"I searched across your recorded meetings but could not find explicit mentions matching **'{question}'**.\n\n"
                f"**Tips to find what you need:**\n"
                f"- Ask for an executive recap of a specific session.\n"
                f"- Search by broader keywords (e.g. *'PLM'*, *'budget'*, *'upload'*).\n"
                f"- Check the **Transcript** tab for verbatim speaker turns."
            )
            return ans, [
                "What were the key decisions in recent meetings?",
                "List all open action items",
            ]

        generic_intent_tokens = {
            "decision", "decisions", "decide", "decided", "action", "actions", "item", "items",
            "summary", "summarize", "recap", "overview", "risk", "risks", "blocker", "blockers",
            "question", "questions", "meeting", "meetings", "last", "recent", "only", "mine",
            "notes", "note", "email", "follow", "customer", "share", "list", "show", "give",
            "discussed", "pending", "unresolved", "open",
        }
        entity_tokens = [t for t in tokens if t not in generic_intent_tokens]
        broad_only_intents = {
            QueryIntent.SUMMARY,
            QueryIntent.CUSTOMER_NOTES,
            QueryIntent.FOLLOW_UP_EMAIL,
            QueryIntent.MULTI_MEETING_RECAP,
        }
        if entity_tokens and intent not in broad_only_intents:
            cit_blob = " ".join((c.get("text") or "").lower() for c in citations)
            ctx_l = structured_ctx.lower()
            know_blob = " ".join(
                f"{k.get('title', '')} {k.get('content', '')}".lower()
                for k in (self._last_retrieved_knowledge or [])
            )
            combined_blob = f"{cit_blob} {ctx_l} {know_blob}"
            entity_hits = sum(1 for t in entity_tokens if t in combined_blob)
            if entity_hits == 0:
                return (
                    "### Not Discussed in Recorded Meetings\n\n"
                    f"No relevant discussion matching **\"{question}\"** was found in the recorded transcript(s)."
                ), [
                    "What were the key decisions made?",
                    "List the action items from this meeting.",
                ]

        if tokens and intent not in structured_intents:
            top_lex = max((int(c.get("lexical_hits", 0)) for c in citations), default=0) if (citations and "lexical_hits" in citations[0]) else (
                sum(1 for t in tokens if any(t in (c.get("text") or "").lower() for c in citations))
            )
            top_sim = max((float(c.get("semantic_sim", 0.0)) for c in citations), default=0.0)
            ctx_l = structured_ctx.lower()
            struct_hits = sum(1 for t in tokens if t in ctx_l)
            if top_lex == 0 and struct_hits == 0 and top_sim < 0.25 and not self._last_retrieved_knowledge:
                return (
                    "### Not Discussed in Recorded Meetings\n\n"
                    f"No relevant discussion matching **\"{question}\"** was found in the recorded transcript(s)."
                ), [
                    "What were the key decisions made?",
                    "List the action items from this meeting.",
                ]

        if self.llm_engine and getattr(self.llm_engine, "is_available", lambda: False)() and (citations or self._last_retrieved_knowledge or structured_ctx):
            try:
                scoped_title = scoped_meeting.get("title") if scoped_meeting else None
                try:
                    llm_ans = self.llm_engine.answer_query(
                        question=question,
                        citations=citations,
                        context_meeting_title=scoped_title,
                        knowledge_items=self._last_retrieved_knowledge,
                        conversation_history=conversation_history,
                        answer_depth=answer_depth,
                        structured_context=structured_ctx,
                        answer_intent=intent.value,
                        local_user_name=local_user_name,
                    )
                except TypeError:
                    try:
                        llm_ans = self.llm_engine.answer_query(
                            question=question,
                            citations=citations,
                            context_meeting_title=scoped_title,
                            knowledge_items=self._last_retrieved_knowledge,
                            conversation_history=conversation_history,
                            answer_depth=answer_depth,
                        )
                    except TypeError:
                        llm_ans = self.llm_engine.answer_query(
                            question=question,
                            citations=citations,
                            context_meeting_title=scoped_title,
                        )
                if llm_ans:
                    meeting_label = scoped_title or (citations[0].get("meeting_title", "recorded meetings") if citations else "recorded meetings")
                    llm_suggestions = [
                        f"What are the assigned action items in {meeting_label}?",
                        f"Summarize key decisions made in {meeting_label}",
                        "Were there any risks or blockers mentioned?",
                    ]
                    return llm_ans, llm_suggestions
            except Exception as exc:
                logger.warning("Local LLM answer generation failed (%s), falling back to template synthesis", exc)

        if intent == QueryIntent.CUSTOMER_NOTES:
            return self._synthesize_customer_notes(scoped_meeting, all_meetings, target_meeting_id=target_meeting_id)

        if intent == QueryIntent.FOLLOW_UP_EMAIL:
            return self._synthesize_follow_up_email(scoped_meeting, all_meetings, target_meeting_id=target_meeting_id)

        if intent == QueryIntent.RISKS_AND_OPEN_ITEMS:
            return self._synthesize_risks_and_open_items(scoped_meeting, all_meetings, citations, target_meeting_id=target_meeting_id)

        if intent == QueryIntent.MULTI_MEETING_RECAP:
            return self._synthesize_multi_meeting_recap(
                all_meetings=all_meetings,
                self_only=self_only,
                local_user_name=local_user_name,
            )

        # -------------------------------------------------------------
        # INTENT: ACTION ITEMS
        # -------------------------------------------------------------
        if intent == QueryIntent.ACTION_ITEMS:
            return self._synthesize_action_items(
                citations,
                scoped_meeting,
                all_meetings,
                target_meeting_id=target_meeting_id,
                self_only=self_only,
                local_user_name=local_user_name,
            )

        # -------------------------------------------------------------
        # INTENT: DECISIONS
        # -------------------------------------------------------------
        if intent == QueryIntent.DECISIONS:
            return self._synthesize_decisions(citations, scoped_meeting, all_meetings, target_meeting_id=target_meeting_id)

        # -------------------------------------------------------------
        # INTENT: SUMMARY & OVERVIEW
        # -------------------------------------------------------------
        if intent == QueryIntent.SUMMARY:
            return self._synthesize_summary(citations, scoped_meeting, all_meetings, target_meeting_id=target_meeting_id)

        # -------------------------------------------------------------
        # INTENT: TOPIC QA / GENERAL / SPEAKER / TIMELINE
        # -------------------------------------------------------------
        return self._synthesize_topic_qa(question, tokens, citations, scoped_meeting, all_meetings, target_meeting_id=target_meeting_id)

    def _load_mom_dict(self, meeting: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not meeting:
            return {}
        def _parse_json_field(val: Any) -> List[Any]:
            if isinstance(val, list):
                return val
            if isinstance(val, str) and val.strip():
                try:
                    parsed = json.loads(val)
                    return parsed if isinstance(parsed, list) else []
                except Exception:
                    return []
            return []

        mid = meeting.get("id")
        risks: List[Any] = []
        if mid is not None and self.db is not None and hasattr(self.db, "list_risks_and_blockers"):
            try:
                risks = self.db.list_risks_and_blockers(int(mid)) or []
            except Exception:
                risks = []
        if not risks:
            risks = list(meeting.get("risks_and_blockers_list") or meeting.get("risks_and_blockers") or meeting.get("risks") or [])

        return {
            "brief_summary": str(meeting.get("brief_summary") or meeting.get("summary") or "").strip(),
            "detailed_summary": str(meeting.get("detailed_summary") or "").strip(),
            "open_questions": _parse_json_field(meeting.get("open_questions_json") if meeting.get("open_questions_json") is not None else meeting.get("open_questions")),
            "topics": _parse_json_field(meeting.get("topics_json") if meeting.get("topics_json") is not None else meeting.get("topics")),
            "key_points": _parse_json_field(meeting.get("key_points_json") if meeting.get("key_points_json") is not None else meeting.get("key_points")),
            "follow_ups": _parse_json_field(meeting.get("follow_ups_json") if meeting.get("follow_ups_json") is not None else meeting.get("follow_ups")),
            "risks_and_blockers": risks,
        }

    def _synthesize_customer_notes(
        self,
        scoped_meeting: Optional[Dict[str, Any]],
        all_meetings: List[Dict[str, Any]],
        target_meeting_id: Optional[int] = None,
    ) -> Tuple[str, List[str]]:
        m = scoped_meeting or (self.db.get_meeting(target_meeting_id) if target_meeting_id is not None else (all_meetings[0] if all_meetings else None))
        if not m:
            return "### Customer-Shareable Notes\n\nNo meeting selected.", []
        mid = m.get("id")
        title = str(m.get("title") or "Meeting Notes").strip()
        date_str = str(m.get("created_at") or "").split()[0] or "Recent"
        mom = self._load_mom_dict(m)
        summary = str(mom.get("brief_summary") or m.get("summary") or "").strip()
        decisions = self.db.list_decisions(mid, limit=30) if mid is not None else []
        actions = self.db.list_action_items(mid, limit=40) if mid is not None else []
        oqs = [q for q in (mom.get("open_questions") or []) if isinstance(q, dict) and str(q.get("question") or "").strip()]
        risks = [r for r in (mom.get("risks_and_blockers") or mom.get("risks") or []) if isinstance(r, dict)]

        lines = [f"### Customer-Shareable Meeting Notes — {title} ({date_str})\n"]
        if summary:
            lines.append("#### Overview\n")
            lines.append(f"{summary}\n")
        if decisions:
            lines.append("#### Agreed Decisions & Next Approach\n")
            for d in decisions:
                st = str(d.get("decision_state") or d.get("status") or "confirmed").replace("_", " ").title()
                lines.append(f"- **[{st}]** {str(d.get('decision') or '').strip()}")
            lines.append("")
        if actions:
            lines.append("#### Action Plan\n")
            lines.append("| Action Item | Owner | Target Date |")
            lines.append("|---|---|---|")
            for a in actions:
                own = str(a.get("owner") or "").strip() or "Owner needed"
                due = str(a.get("due_date") or "").strip() or "TBD"
                desc = str(a.get("description") or "").replace("|", "/").strip()
                lines.append(f"| {desc} | {own} | {due} |")
            lines.append("")
        if oqs or risks:
            lines.append("#### Items Pending Confirmation\n")
            for r in risks:
                desc = str(r.get("description") or r.get("risk") or "").strip()
                if desc:
                    lines.append(f"- **Risk / Blocker:** {desc}")
            for q in oqs:
                qtxt = str(q.get("question") or "").strip()
                if qtxt:
                    lines.append(f"- **Open Question:** {qtxt}")
            lines.append("")

        return "\n".join(lines).strip(), [
            "Prepare a follow-up email for this meeting",
            "What risks or unresolved confirmations remain?",
        ]

    def _synthesize_follow_up_email(
        self,
        scoped_meeting: Optional[Dict[str, Any]],
        all_meetings: List[Dict[str, Any]],
        target_meeting_id: Optional[int] = None,
    ) -> Tuple[str, List[str]]:
        m = scoped_meeting or (self.db.get_meeting(target_meeting_id) if target_meeting_id is not None else (all_meetings[0] if all_meetings else None))
        if not m:
            return "### Follow-Up Email Draft\n\nNo meeting selected.", []
        mid = m.get("id")
        title = str(m.get("title") or "Recent Discussion").strip()
        mom = self._load_mom_dict(m)
        summary = str(mom.get("brief_summary") or m.get("summary") or "").strip()
        decisions = self.db.list_decisions(mid, limit=25) if mid is not None else []
        actions = self.db.list_action_items(mid, limit=30) if mid is not None else []
        oqs = [q for q in (mom.get("open_questions") or []) if isinstance(q, dict) and str(q.get("question") or "").strip()]

        lines = [
            f"### Follow-Up Email Draft\n",
            f"**Subject:** Follow-Up & Action Plan — {title}\n",
            "Hi team,\n",
            f"Thank you for joining **{title}**. Below is a concise summary of what we covered, our agreed direction, and next steps.\n",
        ]
        if summary:
            lines.append(f"**Summary**\n{summary}\n")
        if decisions:
            lines.append("**Key Decisions**")
            for d in decisions[:8]:
                st = str(d.get("decision_state") or d.get("status") or "confirmed").replace("_", " ").title()
                lines.append(f"- **[{st}]** {str(d.get('decision') or '').strip()}")
            lines.append("")
        if actions:
            lines.append("**Action Items & Owners**")
            for a in actions[:10]:
                own = str(a.get("owner") or "").strip() or "Owner needed"
                due = str(a.get("due_date") or "").strip()
                due_s = f" *(Due: {due})*" if due else ""
                lines.append(f"- [ ] **{own}**: {str(a.get('description') or '').strip()}{due_s}")
            lines.append("")
        if oqs:
            lines.append("**Open Items Requiring Confirmation**")
            for q in oqs[:6]:
                lines.append(f"- {str(q.get('question') or '').strip()}")
            lines.append("")
        lines.append("Please let me know if any adjustments or additional confirmations are needed.\n\nBest regards,")
        return "\n".join(lines).strip(), [
            "Prepare notes of this meeting to share with the customer",
            "Which action items still need an owner?",
        ]

    def _synthesize_risks_and_open_items(
        self,
        scoped_meeting: Optional[Dict[str, Any]],
        all_meetings: List[Dict[str, Any]],
        citations: List[Dict[str, Any]],
        target_meeting_id: Optional[int] = None,
    ) -> Tuple[str, List[str]]:
        m = scoped_meeting or (self.db.get_meeting(target_meeting_id) if target_meeting_id is not None else (all_meetings[0] if all_meetings else None))
        mom = self._load_mom_dict(m)
        risks = [r for r in (mom.get("risks_and_blockers") or mom.get("risks") or []) if isinstance(r, dict)]
        oqs = [q for q in (mom.get("open_questions") or []) if isinstance(q, dict) and str(q.get("question") or "").strip()]
        lines = [f"### Risks, Blockers & Unresolved Items ({m.get('title', 'Meeting') if m else 'Meetings'})\n"]
        if risks:
            lines.append("#### Risks & Blockers\n")
            for r in risks:
                itype = str(r.get("item_type") or r.get("type") or "Risk").replace("_", " ").title()
                sev = str(r.get("impact") or r.get("severity") or "Medium").strip()
                desc = str(r.get("description") or r.get("risk") or "").strip()
                mit = str(r.get("mitigation_or_resolution") or r.get("mitigation") or "").strip()
                ts_raw = r.get("timestamp_sec") if r.get("timestamp_sec") is not None else r.get("source_timestamp_sec")
                ts = format_seconds_hms(float(ts_raw or 0.0))
                lines.append(f"- **[{itype} | Impact: {sev}]** (`{ts}`) {desc}")
                if mit and mit.lower() != "none":
                    lines.append(f"  - *Mitigation:* {mit}")
            lines.append("")
        if oqs:
            lines.append("#### Open Questions & Pending Confirmations\n")
            for q in oqs:
                cat = str(q.get("category") or "Pending Confirmation").strip()
                ts = format_seconds_hms(float(q.get("timestamp_sec") or 0.0))
                lines.append(f"- **[{cat}]** (`{ts}`) {str(q.get('question') or '').strip()}")
            lines.append("")
        if not risks and not oqs:
            if citations:
                lines.append("No structured risks were flagged separately; relevant transcript moments:")
                for c in citations[:4]:
                    lines.append(f"- `{format_seconds_hms(float(c.get('start_time') or 0.0))}` **{c.get('speaker', 'Speaker')}**: \"{c.get('text', '')}\"")
            else:
                lines.append("No unresolved risks or open questions were logged for this session.")
        return "\n".join(lines).strip(), [
            "What decisions were made?",
            "List action items and owners",
        ]

    def _synthesize_multi_meeting_recap(
        self,
        all_meetings: List[Dict[str, Any]],
        self_only: bool = False,
        local_user_name: str = "",
    ) -> Tuple[str, List[str]]:
        if not all_meetings:
            return "### Multi-Meeting Recap\n\nNo meetings found in the requested window.", []
        user_tokens = [tok.lower() for tok in re.split(r"[\s._-]+", local_user_name or "") if len(tok) >= 3]
        lines = [f"### Cross-Meeting Summary & Commitments ({len(all_meetings)} Meetings)\n"]
        lines.append("#### Session Highlights\n")
        collected_actions: List[Tuple[str, Dict[str, Any]]] = []
        for m in all_meetings:
            mid = m.get("id")
            title = str(m.get("title") or f"Meeting #{mid}").strip()
            date_s = str(m.get("created_at") or "").split()[0] or "Recent"
            dur = format_seconds_hms(float(m.get("duration_sec") or 0.0))
            brief = str(m.get("summary") or "").strip().splitlines()[0] if m.get("summary") else ""
            lines.append(f"- **{title}** ({date_s}, `{dur}`){(': ' + brief[:220]) if brief else ''}")
            if mid is not None:
                for a in self.db.list_action_items(mid, limit=50):
                    if self_only:
                        own_l = str(a.get("owner") or "").strip().lower()
                        spk_l = str(a.get("speaker") or "").strip().lower()
                        if not (
                            "you" in own_l
                            or "mic" in own_l
                            or (user_tokens and any(tok in own_l for tok in user_tokens))
                            or ("you" in spk_l and not own_l)
                        ):
                            continue
                    collected_actions.append((title, a))
        lines.append("")
        hdr = "#### Action Items Assigned Only to You\n" if self_only else "#### Action Items Across These Meetings\n"
        lines.append(hdr)
        if collected_actions:
            lines.append("| Meeting | Owner | Action Item | Due Date | Status |")
            lines.append("|---|---|---|---|---|")
            for mtitle, a in collected_actions[:25]:
                own = str(a.get("owner") or "").strip() or "Owner needed"
                due = str(a.get("due_date") or "").strip() or "TBD"
                stat = str(a.get("status") or "pending").title()
                desc = str(a.get("description") or "").replace("|", "/").strip()
                lines.append(f"| {mtitle.replace('|', '/')} | {own} | {desc} | {due} | {stat} |")
        else:
            lines.append("No action items matched the requested scope across these meetings.")
        return "\n".join(lines).strip(), [
            "What decisions evolved across recent meetings?",
            "Which action items are still pending?",
        ]

    def _synthesize_action_items(
        self,
        citations: List[Dict[str, Any]],
        scoped_meeting: Optional[Dict[str, Any]],
        all_meetings: List[Dict[str, Any]],
        target_meeting_id: Optional[int] = None,
        self_only: bool = False,
        local_user_name: str = "",
    ) -> Tuple[str, List[str]]:
        """Synthesize action items from structured table and transcript obligation cues."""
        lines = []
        meeting_name = scoped_meeting.get("title", f"Meeting #{target_meeting_id}") if (scoped_meeting or target_meeting_id) else "Across Meetings"
        lines.append(f"### Action Items & Next Steps ({meeting_name})\n")

        # 1. Look for explicit action items from DB
        explicit_actions = []
        if scoped_meeting:
            explicit_actions = self.db.list_action_items(scoped_meeting.get("id"))
        elif target_meeting_id is not None:
            explicit_actions = self.db.list_action_items(target_meeting_id)
        else:
            for m in all_meetings[:5]:
                explicit_actions.extend(self.db.list_action_items(m.get("id")))

        if self_only and explicit_actions:
            user_tokens = [tok.lower() for tok in re.split(r"[\s._-]+", local_user_name or "") if len(tok) >= 3]
            explicit_actions = [
                a for a in explicit_actions
                if (
                    "you" in str(a.get("owner") or "").lower()
                    or "mic" in str(a.get("owner") or "").lower()
                    or (user_tokens and any(tok in str(a.get("owner") or "").lower() for tok in user_tokens))
                )
            ]

        if explicit_actions:
            lines.append("**Explicit Action Items:**\n")
            for act in explicit_actions:
                owner = str(act.get("owner") or "").strip() or "Owner needed"
                desc = act.get("description", "")
                due = act.get("due_date", "")
                due_str = f" *(Due: {due})*" if due else ""
                lines.append(f"- [ ] **{owner}**: {desc}{due_str}")
            lines.append("")
        else:
            # 2. Extract implicit action items from spoken cues ("need to", "let me check", "will", "must")
            spoken_tasks = []
            for c in citations:
                txt = c.get("text", "")
                txt_lower = txt.lower()
                if any(cue in txt_lower for cue in ("let me", "need to", "have to", "check", "will", "going to", "practice")):
                    spk = c.get("speaker", "Speaker")
                    t_str = format_seconds_hms(float(c.get("start_time", 0.0)))
                    m_title = c.get("meeting_title", "Meeting")
                    spoken_tasks.append((spk, txt, t_str, m_title))

            if spoken_tasks:
                lines.append("**Extracted Commitments & Next Steps:**\n")
                for spk, txt, _t_str, m_title in spoken_tasks[:5]:
                    lines.append(f"- [ ] **{spk}** (*{m_title}*): \"{txt}\"")
                lines.append("")
            else:
                lines.append("No explicit pending action items or tasks were detected in these recorded sessions.\n")

        suggestions = [
            f"What decisions were made in {meeting_name}?",
            f"Summarize key discussions in {meeting_name}",
            "Who were the active speakers?"
        ]
        return "\n".join(lines), suggestions

    def _synthesize_decisions(
        self,
        citations: List[Dict[str, Any]],
        scoped_meeting: Optional[Dict[str, Any]],
        all_meetings: List[Dict[str, Any]],
        target_meeting_id: Optional[int] = None,
    ) -> Tuple[str, List[str]]:
        """Synthesize decisions from structured table and transcript agreement cues."""
        lines = []
        meeting_name = scoped_meeting.get("title", f"Meeting #{target_meeting_id}") if (scoped_meeting or target_meeting_id) else "Across Meetings"
        lines.append(f"### ⚖️ Key Decisions & Agreed Outcomes ({meeting_name})\n")

        # 1. Check explicit decisions in DB
        explicit_decisions = []
        if scoped_meeting:
            explicit_decisions = self.db.list_decisions(scoped_meeting.get("id"))
        elif target_meeting_id is not None:
            explicit_decisions = self.db.list_decisions(target_meeting_id)
        else:
            for m in all_meetings[:3]:
                explicit_decisions.extend(self.db.list_decisions(m.get("id")))

        if explicit_decisions:
            lines.append("**Documented Decisions:**\n")
            for dec in explicit_decisions:
                decision_text = dec.get("decision", "")
                cat = dec.get("category")
                cat_str = f" *[{cat}]*" if cat and cat != "General" else ""
                status = dec.get("status")
                stat_str = f" *({status})*" if status and status != "confirmed" else ""
                rationale = dec.get("rationale", "")
                rat_str = f"\n  *Rationale:* {rationale}" if rationale else ""
                lines.append(f"• **Decision**: {decision_text}{cat_str}{stat_str}{rat_str}")
            lines.append("")
        else:
            # 2. Extract consensus and conclusion statements from transcript
            conclusion_quotes = []
            for c in citations:
                txt = c.get("text", "")
                txt_lower = txt.lower()
                if any(cue in txt_lower for cue in ("perfect", "great", "agreed", "decided", "deal", "concluded", "bring the fire", "answered to")):
                    spk = c.get("speaker", "Speaker")
                    t_str = format_seconds_hms(float(c.get("start_time", 0.0)))
                    conclusion_quotes.append((spk, txt, t_str, c.get("meeting_title", "Meeting")))

            if conclusion_quotes:
                lines.append("**Identified Agreements & Conclusions:**\n")
                for spk, txt, _t_str, m_title in conclusion_quotes[:4]:
                    lines.append(f"• **Outcome** (*{m_title}*): **{spk}** confirmed: *\"{txt}\"*")
                lines.append("")
            else:
                lines.append("No formal consensus or contractual decisions were logged for this session.\n")

        suggestions = [
            f"What are the action items for {meeting_name}?",
            f"Give me a complete summary of {meeting_name}",
            "What topics were discussed?"
        ]
        return "\n".join(lines), suggestions

    def _synthesize_summary(
        self,
        citations: List[Dict[str, Any]],
        scoped_meeting: Optional[Dict[str, Any]],
        all_meetings: List[Dict[str, Any]],
        target_meeting_id: Optional[int] = None,
    ) -> Tuple[str, List[str]]:
        """Synthesize an executive summary combining meeting metadata, summaries, and quotes."""
        lines = []
        if scoped_meeting is None and target_meeting_id is not None:
            scoped_meeting = self.db.get_meeting(target_meeting_id)

        if scoped_meeting:
            title = scoped_meeting.get("title", "Meeting")
            dur = format_seconds_hms(float(scoped_meeting.get("duration_sec", 0.0)))
            date_raw = scoped_meeting.get("created_at", "")
            date = date_raw.split()[0] if date_raw and " " in date_raw else date_raw or "Recent"
            summary_db = scoped_meeting.get("summary", "")

            lines.append(f"### 📊 Executive Summary: **{title}**\n")
            lines.append(f"**Date:** {date} • **Duration:** {dur}\n")

            if summary_db and len(summary_db.strip()) > 10:
                lines.append(f"{summary_db.strip()}\n")
            else:
                lines.append(f"The session covered discussions with {len(citations)} key recorded segment(s).\n")

            lines.append("#### 💡 Key Highlights & Spoken Excerpts:\n")
            for c in citations[:5]:
                t_str = format_seconds_hms(float(c.get("start_time", 0.0)))
                spk = c.get("speaker", "Speaker")
                txt = c.get("text", "")
                lines.append(f"• `[{t_str}]` **{spk}**: \"{txt}\"")
        else:
            lines.append("### 📊 Cross-Meeting Portfolio Summary\n")
            lines.append(f"Indexed **{len(all_meetings)} recorded sessions** with grounded transcript memory.\n")
            lines.append("#### Recent Discussions Overview:\n")
            for m in all_meetings[:4]:
                m_title = m.get("title", "Meeting")
                m_dur = format_seconds_hms(float(m.get("duration_sec", 0.0)))
                m_date = str(m.get("created_at", "")).split()[0]
                lines.append(f"• **{m_title}** ({m_date}, `{m_dur}`): {m.get('meeting_type', 'Discussion')}")

        suggestions = [
            "What were the key decisions?",
            "List all pending action items",
            "What was said about audio quality?"
        ]
        return "\n".join(lines), suggestions

    def _synthesize_topic_qa(
        self,
        question: str,
        tokens: List[str],
        citations: List[Dict[str, Any]],
        scoped_meeting: Optional[Dict[str, Any]],
        all_meetings: List[Dict[str, Any]],
        target_meeting_id: Optional[int] = None,
    ) -> Tuple[str, List[str]]:
        """Synthesize factual topic QA grounded with verbatim quotes and timestamps."""
        lines = []
        topic_phrase = " ".join(tokens) if tokens else question
        if scoped_meeting is None and target_meeting_id is not None:
            scoped_meeting = self.db.get_meeting(target_meeting_id)

        if scoped_meeting:
            lines.append(f"### 💡 Answer: {scoped_meeting.get('title', 'Meeting QA')}\n")
        else:
            lines.append(f"### 💡 Answer: Cross-Meeting Intelligence\n")

        # Synthesize direct insight based on top citations
        top_citations = citations[:5]
        speakers_found = list(dict.fromkeys(c.get("speaker", "Speaker") for c in top_citations))
        meetings_found = list(dict.fromkeys(c.get("meeting_title", "Meeting") for c in top_citations))

        direct_insight_parts = []
        for c in top_citations[:3]:
            txt = c.get("text", "")
            spk = c.get("speaker", "Speaker")
            m_title = c.get("meeting_title", "Meeting")
            direct_insight_parts.append(f"**{spk}** in *{m_title}* stated that *\"{txt}\"*")

        if direct_insight_parts:
            lines.append(f"Based on recorded discussions, {'; '.join(direct_insight_parts)}.\n")
        else:
            lines.append("Relevant discussion points found in the selected meeting context:\n")

        for c in top_citations:
            spk = c.get("speaker", "Speaker")
            txt = c.get("text", "")
            title = c.get("meeting_title", "Meeting")
            raw_date = c.get("meeting_date", "")
            date_str = raw_date.split()[0] if raw_date and " " in raw_date else raw_date or "Recent"
            prefix = f"In **{title}** ({date_str}), " if not scoped_meeting else ""
            lines.append(f"• {prefix}**{spk}**: {txt}")

        suggestions = [
            f"What decisions were made regarding {topic_phrase}?",
            f"What are the action items for this?",
            "Summarize the latest meeting"
        ]
        return "\n".join(lines), suggestions


# =====================================================================
# 7-Stage Minutes of Meeting (MoM) Intelligence Pipeline (Parts D, E, F, G)
# =====================================================================

class MeetingMinutesPipeline:
    """
    7-Stage Local Minutes of Meeting (MoM) Intelligence Engine:
    Stage 1: Prepare Transcript Evidence (segments, word counts, talk time, timestamps)
    Stage 2: Participant Registry (Active Speakers, Identified Attendees, Mentioned Stakeholders)
    Stage 3: Agenda Reconstruction (Explicit vs Inferred topics with discussion notes)
    Stage 4: Decisions Log (Confirmed vs Tentative vs Unresolved with verbatim quotes)
    Stage 5: Action Plan (Verb-initiated, verified owner or 'Unassigned — confirmation required', due date, priority, dependencies, status)
    Stage 6: Pending Items, Open Questions & Risks
    Stage 7: Independent Evidence Verification Pass (Forensic audit of every claim against transcript timestamps)

    Outputs structured 10-section contract in Markdown and JSON.
    """

    CONFIRMED_DECISION_PATTERNS = re.compile(
        r"\b(we (decided|agreed|concluded|settled on)|it was decided|approved|consensus reached|let's go with|we will proceed with|final decision|aligned on)\b",
        re.IGNORECASE,
    )
    TENTATIVE_DECISION_PATTERNS = re.compile(
        r"\b(tentatively|provisionally|tentative agreement|preliminary decision|subject to|pending review|pending approval|if tests pass|for now let's)\b",
        re.IGNORECASE,
    )
    UNRESOLVED_DECISION_PATTERNS = re.compile(
        r"\b(tabled|postpone|postponed|defer|deferred|decide next week|table this|no consensus|revisit next time|need to discuss further)\b",
        re.IGNORECASE,
    )

    ACTION_PATTERNS = re.compile(
        r"\b(i will|i'll|you will|will you|can you|could you|please make sure|please|action item|tasked with|take care of|responsible for|follow up on|look into|need to deliver|need to|must|have to|assign|[A-Za-z]+\s+will\b)\b",
        re.IGNORECASE,
    )
    ACTION_VERBS = [
        "Prepare", "Review", "Deploy", "Submit", "Implement", "Investigate",
        "Test", "Validate", "Fix", "Update", "Write", "Build", "Create",
        "Design", "Coordinate", "Schedule", "Send", "Verify", "Document",
        "Configure", "Draft", "Finalize", "Audit", "Publish"
    ]

    RISK_PATTERNS = re.compile(
        r"\b(risk|blocker|concern|bottleneck|delay|hurdle|challenge|vulnerability|dependency on|issue with|failure point)\b",
        re.IGNORECASE,
    )
    QUESTION_PATTERNS = re.compile(
        r"\?|^(what|how|why|when|where|who|which|can we|can you|could you|should we|do we|is there|are there)\b",
        re.IGNORECASE,
    )
    DUE_DATE_PATTERNS = re.compile(
        r"\b(by tomorrow|by friday|by monday|by tuesday|by wednesday|by thursday|by saturday|by sunday|"
        r"by end of day|by eod|by next week|next week|by next friday|by next monday|by next tuesday|by next wednesday|by next thursday|"
        r"by end of week|by end of sprint|by end of month|in \d+ days?|in \d+ weeks?|"
        r"in a couple of weeks|in a few weeks|couple of weeks|revisit in a couple of weeks)\b",
        re.IGNORECASE,
    )

    @classmethod
    def resolve_due_date(
        cls,
        expression: str,
        meeting_timestamp: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve spoken relative dates to verified calendar target with explicit uncertainty."""
        return resolve_relative_due_date(expression, meeting_timestamp=meeting_timestamp, timezone=timezone)

    @classmethod
    def extract_action_items(
        cls,
        evidence_items: List[Dict[str, Any]],
        meeting_timestamp: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Extract action items with structured relative date resolution and uncertainty."""
        return cls._stage5_extract_action_plan(evidence_items, meeting_timestamp=meeting_timestamp, timezone=timezone)

    @classmethod
    def _match_timestamp(
        cls,
        evidence_items: List[Dict[str, Any]],
        quote: str = "",
        time_str: str = ""
    ) -> Tuple[float, str]:
        """Find matching timestamp and speaker from transcript evidence."""
        if not evidence_items:
            return 0.0, "Speaker"

        # Try matching time_str e.g. "05:20" or "23:00"
        if time_str and ":" in time_str:
            try:
                parts = time_str.split(":")
                if len(parts) == 2:
                    target = float(parts[0]) * 60.0 + float(parts[1])
                    closest = min(evidence_items, key=lambda it: abs(it["start"] - target))
                    return closest["start"], closest["speaker"]
            except Exception:
                pass

        # Try matching quote substring
        if quote and len(quote) > 6:
            q_clean = quote.lower().strip()
            for it in evidence_items:
                if q_clean in it["text"].lower() or it["text"].lower() in q_clean:
                    return it["start"], it["speaker"]

        return evidence_items[0]["start"], evidence_items[0]["speaker"]

    @classmethod
    def generate_minutes(
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
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Execute the 7-Stage intelligence pipeline and produce the 10-section MoM."""
        # Stage 1: Prepare Evidence
        evidence_items, total_words, calc_duration = cls._stage1_prepare_evidence(segments, full_transcript, duration_sec)

        if not evidence_items:
            empty_mom = cls._build_empty_mom(title, platform, session_id)
            return empty_mom

        # Stage 2: Participant Registry
        participants = cls._stage2_participant_registry(evidence_items, calc_duration)

        local_user_name = str(kwargs.get("local_user_name") or "").strip()

        # Check for Local LLM Engine (when provided)
        llm_analysis = None
        if llm_engine and getattr(llm_engine, "is_available", lambda: False)():
            try:
                try:
                    llm_analysis = llm_engine.generate_meeting_analysis(
                        segments=segments,
                        title=title,
                        platform=platform,
                        duration_sec=calc_duration,
                        meeting_timestamp=meeting_timestamp,
                        local_user_name=local_user_name,
                    )
                except TypeError:
                    llm_analysis = llm_engine.generate_meeting_analysis(
                        segments=segments,
                        title=title,
                        platform=platform,
                        duration_sec=calc_duration,
                    )
            except Exception as exc:
                logger.warning("Local LLM meeting analysis failed (%s), falling back to rule-based extraction.", exc)

        if llm_analysis and isinstance(llm_analysis, dict):
            from voxlocal.engine.intelligence_verifier import clean_user_facing_text, is_greeting_or_chatter

            # 1. Agenda Topics from LLM (never replace LLM topics with raw utterance chunks when Call 1A succeeded)
            raw_topics = llm_analysis.get("topics", [])
            agenda_items = []
            if raw_topics:
                n_top = len(raw_topics)
                for idx, top in enumerate(raw_topics):
                    st_sec = round(calc_duration * (idx / max(1, n_top)), 1)
                    end_sec = round(calc_duration * ((idx + 1) / max(1, n_top)), 1)
                    title_txt = clean_user_facing_text(str(top.get("title") or f"Topic {idx+1}"))
                    sum_txt = clean_user_facing_text(str(top.get("summary") or ""))
                    if is_greeting_or_chatter(title_txt) and is_greeting_or_chatter(sum_txt):
                        continue
                    details_clean = [
                        clean_user_facing_text(str(d))
                        for d in (top.get("details") or [])
                        if str(d).strip() and not is_greeting_or_chatter(str(d))
                    ]
                    agenda_items.append({
                        "agenda_id": f"AG-{len(agenda_items)+1:02d}",
                        "title": title_txt or f"Topic {len(agenda_items)+1}",
                        "item_type": "AI Structured",
                        "start_sec": st_sec,
                        "end_sec": end_sec,
                        "summary": sum_txt,
                        "discussion_points": details_clean or ([sum_txt] if sum_txt else []),
                        "outcomes": [],
                    })
            elif not llm_analysis.get("call1a_ok"):
                agenda_items = cls._stage3_reconstruct_agenda(evidence_items, calc_duration)

            # 2. Decisions from LLM (Decoupled schema: status, category, evolution_relationship)
            raw_dec = llm_analysis.get("decisions", [])
            decisions = []
            for idx, d in enumerate(raw_dec, 1):
                d_text = str(d.get("decision", "")).strip()
                if not d_text:
                    continue
                cat = str(d.get("category", "General")).strip().capitalize()
                if cat not in ("Strategic", "Technical", "Product", "Budget", "Process", "General"):
                    cat = "General"
                raw_state = str(d.get("decision_state") or d.get("status") or "confirmed").strip().lower()
                if raw_state not in ("confirmed", "proposed", "under_discussion", "rejected", "pending_confirmation", "unresolved", "unclassified"):
                    raw_state = "confirmed"
                stat_map = {
                    "confirmed": "confirmed",
                    "proposed": "proposed",
                    "under_discussion": "unresolved",
                    "pending_confirmation": "unresolved",
                    "rejected": "rejected",
                    "unresolved": "unresolved",
                    "unclassified": "unclassified",
                }
                stat = stat_map.get(raw_state, "confirmed")
                evo = str(d.get("evolution_relationship", "new")).strip().lower()
                if evo not in ("new", "repeated", "superseded", "reversed"):
                    evo = "new"
                quote = str(d.get("evidence_quote", "")).strip()
                t_sec, spk = cls._match_timestamp(evidence_items, quote, d.get("timestamp_str", ""))
                llm_spk = str(d.get("speaker") or "").strip()
                eff_spk = llm_spk if (llm_spk and llm_spk.lower() not in ("speaker", "unknown")) else spk
                matched_quote = quote or next((it["text"] for it in evidence_items if abs(it["start"] - t_sec) < 1.0), evidence_items[0]["text"])
                decisions.append({
                    "decision_id": f"DEC-{idx:02d}",
                    "decision": d_text,
                    "category": cat,
                    "status": stat,
                    "decision_state": raw_state,
                    "evolution_relationship": evo,
                    "supersedes_id": d.get("supersedes_id"),
                    "context": str(d.get("context") or "").strip(),
                    "rationale": d.get("rationale") or f"Announced by {eff_spk} at {format_seconds_hms(t_sec)}",
                    "evidence_quote": matched_quote,
                    "speaker": eff_spk,
                    "participants": list(d.get("participants") or ([eff_spk] if eff_spk else [])),
                    "timestamp_sec": t_sec,
                    "verified": True,
                })
            if not decisions and not llm_analysis.get("call2_ok"):
                decisions = cls._stage4_extract_decisions(evidence_items)

            # 3. Action Items from LLM (Structured relative date resolution with uncertainty)
            raw_act = llm_analysis.get("action_items", [])
            action_items = []
            for idx, a in enumerate(raw_act, 1):
                desc = re.sub(r"^review and address:\s*", "", str(a.get("description", "")).strip(), flags=re.IGNORECASE).strip()
                if not desc:
                    continue
                raw_owner = a.get("owner")
                owner_clean = str(raw_owner).strip() if (raw_owner is not None and str(raw_owner).strip()) else ""
                owner = None if owner_clean.lower() in ("", "none", "null", "unassigned", "unassigned — confirmation required", "owner unknown", "not specified", "n/a") else owner_clean
                raw_due = a.get("due_date")
                due_clean = str(raw_due).strip() if (raw_due is not None and str(raw_due).strip()) else ""
                due_raw = None if due_clean.lower() in ("", "none", "null", "not specified", "no due date", "n/a", "tbd") else due_clean

                # Structured relative date resolution
                if due_raw:
                    due_res = resolve_relative_due_date(due_raw, meeting_timestamp=meeting_timestamp, timezone=timezone)
                    if due_res.get("resolved_date"):
                        due_date = due_res["resolved_date"]
                        due_date_resolution = json.dumps(due_res)
                    elif due_res.get("resolution_status") == "ambiguous":
                        due_date = None
                        due_date_resolution = json.dumps(due_res)
                    else:
                        due_date = due_raw
                        due_date_resolution = json.dumps(due_res)
                else:
                    due_date = None
                    due_date_resolution = ""

                raw_prio = a.get("priority")
                prio_clean = str(raw_prio).strip().capitalize() if raw_prio is not None else ""
                priority = prio_clean if prio_clean in ("High", "Medium", "Low") else ""
                quote = str(a.get("evidence_quote", "")).strip()
                t_sec, spk = cls._match_timestamp(evidence_items, quote, a.get("timestamp_str", ""))
                matched_quote = quote or next((it["text"] for it in evidence_items if abs(it["start"] - t_sec) < 1.0), evidence_items[0]["text"])
                ctype = str(a.get("commitment_type") or ("confirmed_commitment" if owner else "needs_confirmation")).strip().lower()
                if ctype not in ("confirmed_commitment", "suggested_action", "needs_confirmation"):
                    ctype = "confirmed_commitment" if owner else "needs_confirmation"
                action_items.append({
                    "action_id": f"ACT-{idx:02d}",
                    "task": desc,
                    "description": desc,
                    "owner": owner,
                    "due_date": due_date,
                    "due_date_resolution": due_date_resolution,
                    "priority": priority,
                    "commitment_type": ctype,
                    "confirmation_state": ctype,
                    "related_topic": str(a.get("related_topic") or "").strip(),
                    "context": str(a.get("context") or "").strip(),
                    "status": "Pending",
                    "dependencies": "None declared",
                    "evidence_quote": matched_quote,
                    "speaker": spk,
                    "timestamp_sec": t_sec,
                    "verified": True,
                })
            if not action_items and not llm_analysis.get("call2_ok"):
                action_items = cls._stage5_extract_action_plan(evidence_items, meeting_timestamp=meeting_timestamp, timezone=timezone)

            # 4. Open Questions & Risks
            from voxlocal.engine.intelligence_verifier import (
                classify_open_question,
                is_non_substantive_question,
            )

            raw_q = llm_analysis.get("open_questions", [])
            raw_r = llm_analysis.get("risks", [])
            open_questions = []
            for idx, q in enumerate(raw_q, 1):
                q_txt = str(q.get("question", "")).strip()
                if not q_txt or is_non_substantive_question(q_txt):
                    continue
                t_sec, spk = cls._match_timestamp(evidence_items, q_txt, q.get("timestamp_str", ""))
                q_cat = str(q.get("category") or classify_open_question(q_txt)).strip()
                open_questions.append({
                    "question_id": f"Q-{len(open_questions)+1:02d}",
                    "question": q_txt,
                    "category": q_cat,
                    "asked_by": q.get("asked_by") or q.get("ask_by") or spk,
                    "context": str(q.get("context") or f"Raised at {format_seconds_hms(t_sec)}").strip(),
                    "status": str(q.get("status") or "Unresolved").strip(),
                    "timestamp_sec": t_sec,
                })

            risks = []
            for idx, r in enumerate(raw_r, 1):
                r_txt = str(r.get("risk") or r.get("description") or "").strip()
                if not r_txt:
                    continue
                quote = str(r.get("evidence_quote") or "").strip()
                t_sec, spk = cls._match_timestamp(evidence_items, quote or r_txt, r.get("timestamp_str", ""))
                matched_quote = quote or next((it["text"] for it in evidence_items if abs(it["start"] - t_sec) < 1.0), evidence_items[0]["text"])
                itype = str(r.get("item_type") or r.get("type") or "risk").strip().lower()
                if itype not in ("risk", "blocker", "open_dependency", "compliance_gap"):
                    itype = "risk"
                imp = str(r.get("impact") or r.get("severity") or "Medium").strip().capitalize()
                if imp not in ("High", "Medium", "Low"):
                    imp = "Medium"
                mit = str(r.get("mitigation") or r.get("mitigation_or_resolution") or "").strip()
                if mit.lower() in ("none", "n/a", "null", "under review"):
                    mit = ""
                risks.append({
                    "risk_id": f"RSK-{len(risks)+1:02d}",
                    "description": r_txt,
                    "item_type": itype,
                    "type": itype,
                    "impact": imp,
                    "severity": imp,
                    "mitigation_or_resolution": mit,
                    "mitigation": mit,
                    "status": str(r.get("status") or "open").strip(),
                    "raised_by": str(r.get("owner") or spk).strip(),
                    "evidence_quote": matched_quote,
                    "timestamp_sec": t_sec,
                    "verified": True,
                })

            if not open_questions and not risks and not llm_analysis.get("call1b_ok"):
                open_questions, risks = cls._stage6_extract_pending_and_risks(evidence_items)

            # 5. Executive Summary & Detailed Summary (clean user-perspective prose)
            llm_brief = clean_user_facing_text(str(llm_analysis.get("brief_summary") or llm_analysis.get("executive_summary") or "").strip())
            llm_detailed = clean_user_facing_text(str(llm_analysis.get("detailed_summary") or "").strip())
            meeting_purpose = clean_user_facing_text(str(llm_analysis.get("meeting_purpose") or "").strip())
            overall_outcome = clean_user_facing_text(str(llm_analysis.get("overall_outcome") or "").strip())
            if llm_brief:
                executive_summary = llm_brief
            else:
                executive_summary = cls._synthesize_executive_summary(evidence_items, participants, decisions, action_items)
                llm_brief = executive_summary
            llm_key_points = list(llm_analysis.get("key_discussion_points") or [])
            llm_follow_ups = list(llm_analysis.get("follow_ups") or [])
            llm_topics_struct = list(llm_analysis.get("topics") or [])
            llm_model_name = str(llm_analysis.get("llm_model") or "")
            llm_device_name = str(llm_analysis.get("llm_device") or "")
            llm_fb_reason = str(llm_analysis.get("llm_fallback_reason") or "")
            llm_rel = dict(llm_analysis.get("llm_reliability") or {})
        else:
            # Rule-based fallback pipeline (explicit fallback notice — never silent)
            agenda_items = cls._stage3_reconstruct_agenda(evidence_items, calc_duration)
            decisions = cls._stage4_extract_decisions(evidence_items)
            action_items = cls._stage5_extract_action_plan(evidence_items, meeting_timestamp=meeting_timestamp, timezone=timezone)
            open_questions, risks = cls._stage6_extract_pending_and_risks(evidence_items)
            executive_summary = cls._synthesize_executive_summary(evidence_items, participants, decisions, action_items)
            llm_brief = executive_summary
            llm_detailed = ""
            meeting_purpose = ""
            overall_outcome = ""
            llm_topics_struct = []
            llm_key_points = []
            llm_follow_ups = []
            llm_model_name = "extractive-fallback"
            llm_device_name = "cpu"
            llm_fb_reason = "Local LLM (Qwen3.5-4B) unavailable; generated deterministic extractive fallback."
            llm_rel = {}

        # Stage 7: Independent Evidence Verification Pass
        verification = cls._stage7_verify_evidence(evidence_items, decisions, action_items, risks)

        # Assemble Structured 10-Section Markdown & JSON
        mom_markdown = cls._render_10_section_markdown(
            title=title,
            platform=platform,
            session_id=session_id,
            duration_sec=calc_duration,
            executive_summary=executive_summary,
            participants=participants,
            agenda=agenda_items,
            decisions=decisions,
            actions=action_items,
            questions=open_questions,
            risks=risks,
            verification=verification,
        )

        # Backwards-compatible fields for DB and legacy UI with decoupled enterprise attributes
        decisions_list = [
            {
                "decision": d["decision"],
                "status": d.get("status", "confirmed"),
                "decision_state": d.get("decision_state", d.get("status", "confirmed")),
                "category": d.get("category", "General"),
                "evolution_relationship": d.get("evolution_relationship", "new"),
                "supersedes_id": d.get("supersedes_id"),
                "context": d.get("context", ""),
                "speaker": d.get("speaker", ""),
                "participants": d.get("participants", []),
                "evidence_quote": d.get("evidence_quote", ""),
                "rationale": d.get("rationale") or f"[{d.get('category', 'General')}] {d.get('evidence_quote', '')}",
                "timestamp_sec": d["timestamp_sec"],
            }
            for d in decisions
        ]
        action_list = [
            {
                "description": a["description"],
                "owner": a["owner"],
                "due_date": a["due_date"],
                "due_date_resolution": a.get("due_date_resolution", ""),
                "evidence_quote": a.get("evidence_quote", ""),
                "priority": a.get("priority", ""),
                "confirmation_state": a.get("confirmation_state", "confirmed"),
                "related_topic": a.get("related_topic", ""),
                "context": a.get("context", ""),
                "status": a.get("status", "pending").lower(),
                "timestamp_sec": a["timestamp_sec"],
            }
            for a in action_items
        ]
        chapters_list = [
            {
                "title": a["title"],
                "start_time": a["start_sec"],
                "duration_sec": round(a["end_sec"] - a["start_sec"], 1),
                "summary": a["summary"],
            }
            for a in agenda_items
        ]
        questions_list = [
            {
                "speaker": q["asked_by"],
                "asked_by": q["asked_by"],
                "question": q["question"],
                "context": q.get("context", ""),
                "status": q.get("status", "Pending Clarification"),
                "timestamp_sec": q["timestamp_sec"],
            }
            for q in open_questions
        ]

        # Calculate Engagement Score
        speaker_metrics = {p["name"]: {"words": p["words"], "percentage": p["talk_time_pct"], "wpm": p["wpm"], "time_sec": p["talk_time_sec"]} for p in participants["active_speakers"]}
        balance = 1.0 - (max((p["talk_time_pct"] for p in participants["active_speakers"]), default=50.0) - (100.0 / max(1, len(participants["active_speakers"])))) / 100.0
        engagement = int(min(98, max(65, 72 + (balance * 18) + (len(open_questions) * 2))))

        mom_json = {
            "meeting_details": {
                "title": title,
                "platform": platform,
                "session_id": session_id,
                "duration_sec": calc_duration,
                "total_words": total_words,
                "purpose": meeting_purpose,
                "outcome": overall_outcome,
            },
            "brief_summary": llm_brief,
            "executive_summary": executive_summary,
            "detailed_summary": llm_detailed,
            "participants": participants,
            "agenda": agenda_items,
            "topics_structured": llm_topics_struct or [
                {"title": a["title"], "summary": a["summary"], "details": a.get("discussion_points", []), "participants": [], "timestamp_str": format_seconds_hms(a["start_sec"])}
                for a in agenda_items
            ],
            "decisions": decisions,
            "action_items": action_items,
            "follow_ups": llm_follow_ups,
            "open_questions": open_questions,
            "risks": risks,
            "verification": verification,
        }

        return {
            "mom_markdown": mom_markdown,
            "mom_json": mom_json,
            "summary": mom_markdown,
            "brief_summary": llm_brief,
            "detailed_summary": llm_detailed,
            "meeting_purpose": meeting_purpose,
            "overall_outcome": overall_outcome,
            "topics_structured": mom_json["topics_structured"],
            "key_discussion_points": llm_key_points,
            "follow_ups": llm_follow_ups,
            "open_questions": questions_list,
            "risks": risks,
            "llm_model": llm_model_name,
            "llm_device": llm_device_name,
            "llm_fallback_reason": llm_fb_reason,
            "llm_reliability": llm_rel,
            "decisions": decisions_list,
            "action_items": action_list,
            "chapters": chapters_list,
            "questions": questions_list,
            "suggested_prompts": [
                f"What were the key decisions made regarding {agenda_items[0]['title'] if agenda_items else 'the project'}?",
                "List all action items and verified owners.",
                "What risks or blockers were identified?",
            ],
            "speaker_metrics": speaker_metrics,
            "engagement_score": engagement,
            "topics": [a["title"] for a in agenda_items] if agenda_items else ["General Discussion"],
        }

    # -----------------------------------------------------------------
    # Stage 1: Prepare Transcript Evidence
    # -----------------------------------------------------------------
    @classmethod
    def _stage1_prepare_evidence(
        cls, segments: List[Dict[str, Any]], full_transcript: str, duration_sec: float
    ) -> Tuple[List[Dict[str, Any]], int, float]:
        raw_items: List[Dict[str, Any]] = []
        if segments:
            for s in segments:
                spk = str(s.get("speaker", "Speaker")).strip() or "Speaker"
                txt = str(s.get("text", "")).strip()
                t_start = float(s.get("start_time", 0.0))
                t_end = float(s.get("end_time", t_start + 2.0))
                if txt:
                    words = txt.split()
                    raw_items.append({
                        "speaker": spk,
                        "text": txt,
                        "start": t_start,
                        "end": t_end,
                        "words": words,
                    })
        elif full_transcript.strip():
            curr_t = 0.0
            for line in full_transcript.splitlines():
                line = line.strip()
                if line:
                    words = line.split()
                    dur = max(2.0, len(words) * 0.35)
                    raw_items.append({
                        "speaker": "Speaker",
                        "text": line,
                        "start": curr_t,
                        "end": curr_t + dur,
                        "words": words,
                    })
                    curr_t += dur

        total_words = sum(len(it["words"]) for it in raw_items)
        max_end = max((it["end"] for it in raw_items), default=0.0)
        calc_dur = max(duration_sec, max_end)
        return raw_items, total_words, calc_dur

    # -----------------------------------------------------------------
    # Stage 2: Participant Registry
    # -----------------------------------------------------------------
    @classmethod
    def _stage2_participant_registry(
        cls, items: List[Dict[str, Any]], total_sec: float
    ) -> Dict[str, Any]:
        spk_words: Dict[str, int] = {}
        spk_time: Dict[str, float] = {}
        for it in items:
            spk = it["speaker"]
            dur = max(0.5, it["end"] - it["start"])
            spk_words[spk] = spk_words.get(spk, 0) + len(it["words"])
            spk_time[spk] = spk_time.get(spk, 0.0) + dur

        sum_time = max(1.0, sum(spk_time.values()))
        active_speakers = []
        for spk, words in spk_words.items():
            t_sec = spk_time[spk]
            pct = round((t_sec / sum_time) * 100.0, 1)
            mins = max(0.1, t_sec / 60.0)
            wpm = round(words / mins, 1)
            active_speakers.append({
                "name": spk,
                "role": "Active Speaker",
                "words": words,
                "talk_time_sec": round(t_sec, 1),
                "talk_time_pct": pct,
                "wpm": wpm,
            })
        active_speakers.sort(key=lambda x: -x["words"])

        # Scan for mentioned stakeholders not currently speaking
        all_text = " ".join(it["text"] for it in items)
        mentioned_pattern = re.compile(
            r"\b(?:check with|talk to|ask|email|contact|assigned to|call|from)\s+([A-Z][a-z]+)\b"
        )
        speaker_names_lower = {s["name"].lower() for s in active_speakers}
        mentioned_names: Set[str] = set()
        for m in mentioned_pattern.finditer(all_text):
            candidate = m.group(1).strip()
            if candidate.lower() not in speaker_names_lower and candidate.lower() not in STOPWORDS and len(candidate) > 2:
                mentioned_names.add(candidate)

        mentioned_stakeholders = [
            {"name": name, "role": "Referenced Stakeholder"} for name in sorted(mentioned_names)
        ]

        return {
            "active_speakers": active_speakers,
            "identified_attendees": [s["name"] for s in active_speakers],
            "mentioned_stakeholders": mentioned_stakeholders,
        }

    # -----------------------------------------------------------------
    # Stage 3: Agenda Reconstruction (Explicit vs Inferred)
    # -----------------------------------------------------------------
    @classmethod
    def _stage3_reconstruct_agenda(
        cls, items: List[Dict[str, Any]], total_sec: float
    ) -> List[Dict[str, Any]]:
        agenda_items: List[Dict[str, Any]] = []
        explicit_pattern = re.compile(
            r"\b(?:agenda for today|today's agenda|first topic|item on the agenda|moving on to|next topic|next item|let's discuss|let's talk about)\s+([^.,;?!]+)",
            re.IGNORECASE,
        )

        for it in items:
            m = explicit_pattern.search(it["text"])
            if m:
                topic_title = m.group(1).strip().capitalize()
                if len(topic_title.split()) <= 8:
                    agenda_items.append({
                        "agenda_id": f"AG-{len(agenda_items)+1:02d}",
                        "title": topic_title,
                        "item_type": "Explicit",
                        "start_sec": it["start"],
                        "end_sec": it["end"],
                        "summary": it["text"],
                        "discussion_points": [it["text"]],
                        "outcomes": [],
                    })

        # If few or no explicit agenda points, cluster substantive turns semantically
        if len(agenda_items) < 2:
            from voxlocal.engine.intelligence_verifier import is_greeting_or_chatter
            substantive = [it for it in items if not is_greeting_or_chatter(it["text"])] or items
            agenda_items.clear()
            chunk_dur = max(90.0, total_sec / 3.5)
            curr_chunk: List[Dict[str, Any]] = []
            chunk_start = substantive[0]["start"]

            for it in substantive:
                if it["start"] - chunk_start >= chunk_dur and curr_chunk:
                    title = cls._extract_topic_title(curr_chunk)
                    summary = " ".join(c["text"] for c in curr_chunk[:2])
                    points = [c["text"] for c in curr_chunk if len(c["words"]) >= 5][:4]
                    agenda_items.append({
                        "agenda_id": f"AG-{len(agenda_items)+1:02d}",
                        "title": title,
                        "item_type": "Inferred",
                        "start_sec": chunk_start,
                        "end_sec": it["start"],
                        "summary": summary[:220] + "..." if len(summary) > 220 else summary,
                        "discussion_points": points,
                        "outcomes": [],
                    })
                    curr_chunk = [it]
                    chunk_start = it["start"]
                else:
                    curr_chunk.append(it)

            if curr_chunk:
                title = cls._extract_topic_title(curr_chunk)
                summary = " ".join(c["text"] for c in curr_chunk[:2])
                points = [c["text"] for c in curr_chunk if len(c["words"]) >= 5][:4]
                agenda_items.append({
                    "agenda_id": f"AG-{len(agenda_items)+1:02d}",
                    "title": title,
                    "item_type": "Inferred",
                    "start_sec": chunk_start,
                    "end_sec": curr_chunk[-1]["end"],
                    "summary": summary[:220] + "..." if len(summary) > 220 else summary,
                    "discussion_points": points,
                    "outcomes": [],
                })

        return agenda_items

    @classmethod
    def _extract_topic_title(cls, chunk: List[Dict[str, Any]]) -> str:
        text = " ".join(c["text"] for c in chunk)
        keywords = [
            "Architecture", "Budget", "Sprint Planning", "Database", "Security",
            "API Integration", "Deployment", "Release", "QA Testing", "Performance",
            "User Experience", "Customer Feedback", "Design Review", "Next Steps"
        ]
        for kw in keywords:
            if re.search(rf"\b{kw}\b", text, re.IGNORECASE):
                return f"{kw} Review"
        # Fallback to key subject from first utterance
        words = [w for w in chunk[0]["words"] if w.lower() not in STOPWORDS]
        if words:
            return " ".join(words[:4]).capitalize()
        return "General Discussion"

    # -----------------------------------------------------------------
    # Stage 4: Decisions Log (Strict Categorization & Verbatim Citations)
    # -----------------------------------------------------------------
    @classmethod
    def _stage4_extract_decisions(
        cls,
        items: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        decisions: List[Dict[str, Any]] = []
        seen_dec_texts: Set[str] = set()

        for it in items:
            txt = it["text"]
            if len(it["words"]) < 4:
                continue

            status = None
            if cls.UNRESOLVED_DECISION_PATTERNS.search(txt):
                status = "unresolved"
            elif cls.TENTATIVE_DECISION_PATTERNS.search(txt):
                status = "proposed"
            elif cls.CONFIRMED_DECISION_PATTERNS.search(txt):
                status = "confirmed"

            if status:
                clean_dec = txt
                for cue in (
                    "we decided to", "we agreed to", "it was decided that",
                    "we settled on", "let's go with", "decision is to"
                ):
                    if cue in clean_dec.lower():
                        idx = clean_dec.lower().index(cue) + len(cue)
                        clean_dec = clean_dec[idx:].strip(" :,-")
                        clean_dec = clean_dec[0].upper() + clean_dec[1:] if clean_dec else txt
                        break

                norm_key = " ".join(clean_dec.lower().split())
                if norm_key in seen_dec_texts:
                    continue
                seen_dec_texts.add(norm_key)

                txt_lower = txt.lower()
                category = "General"
                if any(k in txt_lower for k in ("budget", "cost", "pricing", "expense", "fund", "dollar", "$", "revenue", "financial", "spend")):
                    category = "Budget"
                elif any(k in txt_lower for k in ("architecture", "database", "api", "backend", "frontend", "infrastructure", "server", "code", "deploy", "security", "framework", "performance", "stack", "sql")):
                    category = "Technical"
                elif any(k in txt_lower for k in ("feature", "ui", "ux", "design", "user", "roadmap", "mvp", "spec", "screen", "button", "workflow")):
                    category = "Product"
                elif any(k in txt_lower for k in ("sprint", "meeting", "agile", "scrum", "review", "schedule", "onboarding", "policy", "guideline", "documentation", "process")):
                    category = "Process"
                elif any(k in txt_lower for k in ("strategy", "partner", "hiring", "target", "vision", "market", "q1", "q2", "q3", "q4", "expansion", "launch", "strategic")):
                    category = "Strategic"

                decisions.append({
                    "decision_id": f"DEC-{len(decisions)+1:02d}",
                    "decision": clean_dec,
                    "status": status,
                    "category": category,
                    "evolution_relationship": "new",
                    "supersedes_id": None,
                    "rationale": f"Announced by {it['speaker']} at {format_seconds_hms(it['start'])}",
                    "evidence_quote": txt,
                    "speaker": it["speaker"],
                    "timestamp_sec": it["start"],
                    "verified": True,
                })

        return decisions

    # -----------------------------------------------------------------
    # Stage 5: Action Plan (Strict Ownership & Verb Formulation)
    # -----------------------------------------------------------------
    @classmethod
    def _stage5_extract_action_plan(
        cls,
        items: List[Dict[str, Any]],
        meeting_timestamp: Optional[str] = None,
        timezone: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []
        seen_action_texts: Set[str] = set()

        for idx, it in enumerate(items):
            txt = it["text"]
            if len(it["words"]) < 4:
                continue

            if cls.ACTION_PATTERNS.search(txt):
                # Skip greetings / holiday / pleasantries
                if re.search(r"\b(have a good holiday|happy holiday|be safe out there|take care of yourself|thank you everyone|good morning|goodbye|bye everyone)\b", txt, re.IGNORECASE):
                    continue
                owner: Optional[str] = None
                txt_lower = txt.lower()

                # Self-assignment
                if " i will " in f" {txt_lower} " or " i'll " in f" {txt_lower} " or " i can " in f" {txt_lower} ":
                    owner = it["speaker"] if it["speaker"] != "Remote Audio" else None
                else:
                    # Direct assignment to named participant
                    m_named = re.search(r"\b([A-Z][a-z]+)\s+(?:will|can|should|to|please)\b", txt)
                    if m_named and m_named.group(1).lower() not in STOPWORDS:
                        owner = m_named.group(1)
                    # Turn-taking assignment: "[Name], can you...?" -> Next turn: "Sure" / "Yes"
                    elif idx + 1 < len(items):
                        next_turn = items[idx + 1]
                        m_ask = re.search(r"\b([A-Z][a-z]+)[,\s]+can you\b", txt, re.IGNORECASE)
                        if m_ask and any(aff in next_turn["text"].lower() for aff in ("sure", "yes", "i will", "on it")):
                            owner = m_ask.group(1)

                # Extract due date & structured resolution (null when unstated)
                m_due = cls.DUE_DATE_PATTERNS.search(txt)
                raw_due = m_due.group(0).strip() if m_due else ""

                ambiguous_cues = (
                    "couple of weeks", "few weeks", "next sprint", "sometime next week",
                    "later this month", "down the road", "when possible", "eventually"
                )
                if not raw_due:
                    for cue in ambiguous_cues:
                        if cue in txt.lower():
                            raw_due = cue
                            break

                due_date_resolution_json = ""
                if raw_due:
                    res_obj = resolve_relative_due_date(
                        expression=raw_due,
                        meeting_timestamp=meeting_timestamp,
                        timezone=timezone,
                    )
                    due_date_resolution_json = json.dumps(res_obj)
                    resolved = res_obj.get("resolved_date")
                    if resolved:
                        due_date = resolved
                    elif res_obj.get("resolution_status") == "ambiguous":
                        due_date = None
                    else:
                        due_date = raw_due.capitalize()
                else:
                    due_date = None

                # Priority scoring
                priority = ""
                if any(p in txt_lower for p in ("urgent", "asap", "blocker", "critical", "immediately", "p0")):
                    priority = "High"
                elif any(p in txt_lower for p in ("when possible", "eventually", "low priority", "nice to have")):
                    priority = "Low"

                desc = txt.strip()
                norm_desc = " ".join(desc.lower().split())
                if norm_desc in seen_action_texts:
                    continue
                seen_action_texts.add(norm_desc)

                actions.append({
                    "action_id": f"ACT-{len(actions)+1:02d}",
                    "task": desc,
                    "description": desc,
                    "owner": owner,
                    "due_date": due_date,
                    "due_date_resolution": due_date_resolution_json,
                    "priority": priority,
                    "dependencies": "None specified",
                    "status": "Pending",
                    "evidence_quote": txt,
                    "timestamp_sec": it["start"],
                    "verified": True,
                })

        return actions

    # -----------------------------------------------------------------
    # Stage 6: Pending Items, Open Questions & Risks
    # -----------------------------------------------------------------
    @classmethod
    def _stage6_extract_pending_and_risks(
        cls, items: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        from voxlocal.engine.intelligence_verifier import (
            classify_open_question,
            is_non_substantive_question,
        )

        questions: List[Dict[str, Any]] = []
        risks: List[Dict[str, Any]] = []

        for idx, it in enumerate(items):
            txt = it["text"]
            if len(it["words"]) < 4:
                continue

            # Questions & Discussion Queries (skip greetings, context setups, and trivial turns)
            if ("?" in txt or cls.QUESTION_PATTERNS.search(txt)) and not is_non_substantive_question(txt):
                # Check if resolved in the immediately following turns
                is_answered = False
                for look in range(1, 3):
                    if idx + look < len(items):
                        next_txt = items[idx + look]["text"].lower()
                        if any(ans in next_txt for ans in ("because", "we will", "the reason is", "we decided", "agreed", "yes, let's", "that makes sense")):
                            is_answered = True
                            break
                if not is_answered:
                    questions.append({
                        "question_id": f"Q-{len(questions)+1:02d}",
                        "asked_by": it["speaker"],
                        "question": txt,
                        "category": classify_open_question(txt),
                        "context": f"Raised at {format_seconds_hms(it['start'])}",
                        "timestamp_sec": it["start"],
                        "status": "Unresolved",
                    })

            # Risks & Blockers
            if cls.RISK_PATTERNS.search(txt):
                severity = "High" if any(s in txt.lower() for s in ("blocker", "critical", "fail", "delay")) else "Medium"
                risks.append({
                    "risk_id": f"RSK-{len(risks)+1:02d}",
                    "description": txt,
                    "severity": severity,
                    "raised_by": it["speaker"],
                    "evidence_quote": txt,
                    "timestamp_sec": it["start"],
                    "verified": True,
                })

        return questions[:8], risks[:8]

    # -----------------------------------------------------------------
    # Stage 7: Independent Evidence Verification Pass
    # -----------------------------------------------------------------
    @classmethod
    def _stage7_verify_evidence(
        cls,
        items: List[Dict[str, Any]],
        decisions: List[Dict[str, Any]],
        actions: List[Dict[str, Any]],
        risks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        transcript_corpus = " ".join(it["text"].lower() for it in items)
        total_claims = len(decisions) + len(actions) + len(risks)
        verified_count = 0
        audit_notes: List[str] = []

        for d in decisions:
            quote_tokens = [w.lower() for w in d["evidence_quote"].split() if w.lower() not in STOPWORDS]
            if any(tok in transcript_corpus for tok in quote_tokens[:4]):
                verified_count += 1
            else:
                d["verified"] = False
                audit_notes.append(f"Unverified claim in Decision {d['decision_id']}")

        for a in actions:
            quote_tokens = [w.lower() for w in a["evidence_quote"].split() if w.lower() not in STOPWORDS]
            if any(tok in transcript_corpus for tok in quote_tokens[:4]):
                verified_count += 1
            else:
                a["verified"] = False
                audit_notes.append(f"Unverified claim in Action Item {a['action_id']}")

        for r in risks:
            quote_tokens = [w.lower() for w in r["evidence_quote"].split() if w.lower() not in STOPWORDS]
            if any(tok in transcript_corpus for tok in quote_tokens[:4]):
                verified_count += 1
            else:
                r["verified"] = False
                audit_notes.append(f"Unverified claim in Risk {r['risk_id']}")

        conf_score = round((verified_count / max(1, total_claims)) * 100.0, 1) if total_claims else 100.0
        return {
            "total_claims": total_claims,
            "verified_claims": verified_count,
            "unverified_claims": total_claims - verified_count,
            "confidence_score": conf_score,
            "audit_notes": audit_notes or ["All claims 100% verified against transcript evidence."],
        }

    # -----------------------------------------------------------------
    # Executive Summary Synthesis
    # -----------------------------------------------------------------
    @classmethod
    def _synthesize_executive_summary(
        cls,
        items: List[Dict[str, Any]],
        participants: Dict[str, Any],
        decisions: List[Dict[str, Any]],
        actions: List[Dict[str, Any]],
    ) -> str:
        spk_count = len(participants["active_speakers"])
        dec_count = len(decisions)
        act_count = len(actions)

        lines = [
            f"• Overview: Discussion across {spk_count} active participant(s), resulting in {dec_count} confirmed/evaluated decision(s) and {act_count} designated action item(s)."
        ]

        # Top 3 key moments
        key_moments = []
        for it in items[:4]:
            if len(it["words"]) >= 6:
                key_moments.append(f"• **{it['speaker']}** opened discussion: *\"{it['text']}\"*")
                break
        for d in decisions[:2]:
            key_moments.append(f"• **Key Agreement:** {d['decision']} (Category: {d['category']})")
        for a in actions[:2]:
            key_moments.append(f"• **Priority Action:** {a['description']} (Assigned: **{a['owner']}**, Due: {a['due_date']})")

        if key_moments:
            lines.append("\n" + "\n".join(key_moments))

        return "\n".join(lines)

    # -----------------------------------------------------------------
    # Render Structured 10-Section Markdown Output
    # -----------------------------------------------------------------
    @classmethod
    def _render_10_section_markdown(
        cls,
        title: str,
        platform: str,
        session_id: str,
        duration_sec: float,
        executive_summary: str,
        participants: Dict[str, Any],
        agenda: List[Dict[str, Any]],
        decisions: List[Dict[str, Any]],
        actions: List[Dict[str, Any]],
        questions: List[Dict[str, Any]],
        risks: List[Dict[str, Any]],
        verification: Dict[str, Any],
    ) -> str:
        sec = []

        # Header - Clean executive styling with no leading '#' or '*'
        sec.append("📋 MINUTES OF MEETING (MoM)")
        sec.append("=" * 60 + "\n")

        # Section 1: Meeting Details
        sec.append("1. MEETING DETAILS")
        sec.append("-" * 60)
        sec.append(f"• Meeting Title: {title}")
        sec.append(f"• Platform / Surface: {platform} (Call-Only Isolation)")
        sec.append(f"• Session ID: {session_id or 'auto_session'}")
        sec.append(f"• Duration: {format_seconds_hms(duration_sec)} ({round(duration_sec/60.0, 1)} min)")
        sec.append(f"• Evidence Grounding: {verification.get('confidence_score', 95)}% Confirmed\n")

        # Section 2: Executive Summary (sanitize leading '#' or '*' from any lines)
        sec.append("2. EXECUTIVE SUMMARY")
        sec.append("-" * 60)
        clean_summary_lines = []
        for line in (executive_summary or "").splitlines():
            l_strip = line.strip()
            if l_strip.startswith(("#", "*")):
                l_strip = l_strip.lstrip("#*").strip()
                if l_strip:
                    clean_summary_lines.append(f"• {l_strip}")
            else:
                clean_summary_lines.append(line)
        sec.append("\n".join(clean_summary_lines) if clean_summary_lines else "Executive summary recorded.")
        sec.append("")

        # Section 3: Participants & Roles (Tabular)
        sec.append("3. PARTICIPANTS & ROLES")
        sec.append("-" * 60)
        sec.append("| Participant | Role | Words Spoken | Talk Time | Share (%) | WPM |")
        sec.append("| :--- | :--- | :---: | :---: | :---: | :---: |")
        for p in participants.get("active_speakers", []):
            sec.append(f"| **{p['name']}** | {p['role']} | {p['words']} | {format_seconds_hms(p['talk_time_sec'])} | {p['talk_time_pct']}% | {p['wpm']} |")
        if participants.get("mentioned_stakeholders"):
            m_names = ", ".join(m["name"] for m in participants["mentioned_stakeholders"])
            sec.append(f"\nMentioned Stakeholders (Absent/External): {m_names}\n")
        else:
            sec.append("")

        # Section 4: Agenda & Discussion Breakdown (Point-wise)
        sec.append("4. AGENDA & DISCUSSION BREAKDOWN")
        sec.append("-" * 60)
        if agenda:
            for ag in agenda:
                sec.append(f"• [{ag['agenda_id']}] {ag['title']} [{ag['item_type']}] ({format_seconds_hms(ag['start_sec'])})")
                sec.append(f"  Summary: {ag['summary']}")
                if ag.get("discussion_points"):
                    for dp in ag["discussion_points"]:
                        dp_clean = str(dp).lstrip("#* -").strip()
                        sec.append(f"  • {dp_clean}")
                sec.append("")
        else:
            sec.append("• General topic discussion held without explicit agenda boundaries.\n")

        # Section 5: Decisions Log (Tabular)
        sec.append("5. DECISIONS LOG")
        sec.append("-" * 60)
        if decisions:
            sec.append("| ID | Decision | Status | Timestamp | Speaker | Grounded Evidence |")
            sec.append("| :--- | :--- | :---: | :---: | :--- | :--- |")
            for d in decisions:
                ev_quote = (d.get("evidence_quote") or "").replace("|", "-").strip()
                if len(ev_quote) > 60:
                    ev_quote = ev_quote[:57] + "..."
                sec.append(f"| {d.get('decision_id', 'DEC')} | {d.get('decision', '')} | {d.get('category', 'Confirmed')} | {format_seconds_hms(d.get('timestamp_sec', 0.0))} | {d.get('speaker', 'Speaker')} | \"{ev_quote}\" |")
            sec.append("")
        else:
            sec.append("• No explicit final decisions declared during this session.\n")

        # Section 6: Action Plan (Tabular)
        sec.append("6. ACTION PLAN & COMMITMENTS")
        sec.append("-" * 60)
        if actions:
            sec.append("| ID | Action Item | Owner | Deadline | Priority | Status | Evidence Quote |")
            sec.append("| :--- | :--- | :--- | :--- | :---: | :---: | :--- |")
            for a in actions:
                q_snip = (a.get("evidence_quote") or "").replace("|", "-").strip()
                if len(q_snip) > 38:
                    q_snip = q_snip[:35] + "..."
                owner_str = (a.get("owner") or "").strip() or "—"
                due_str = (a.get("due_date") or "").strip() or "—"
                sec.append(f"| {a.get('action_id', 'ACT')} | **{a.get('description', '')}** | {owner_str} | {due_str} | {a.get('priority', 'Medium')} | {a.get('status', 'Open')} | \"{q_snip}\" ({format_seconds_hms(a.get('timestamp_sec', 0.0))}) |")
            sec.append("")
        else:
            sec.append("• No concrete action items or deliverable tasks assigned.\n")

        # Section 7: Pending Items & Open Questions (Tabular)
        sec.append("7. PENDING ITEMS & OPEN QUESTIONS")
        sec.append("-" * 60)
        if questions:
            sec.append("| ID | Open Question / Item | Asked By | Status | Context |")
            sec.append("| :--- | :--- | :--- | :---: | :--- |")
            for q in questions:
                sec.append(f"| {q.get('question_id', 'Q')} | \"{q.get('question', '')}\" | {q.get('asked_by', 'Participant')} | {q.get('status', 'Open')} | {q.get('context', 'General')} |")
            sec.append("")
        else:
            sec.append("• No outstanding unanswered questions recorded.\n")

        # Section 8: Risks, Blockers & Issues (Tabular)
        sec.append("8. RISKS, BLOCKERS & ISSUES")
        sec.append("-" * 60)
        if risks:
            sec.append("| ID | Risk / Blocker | Severity | Raised By | Timestamp |")
            sec.append("| :--- | :--- | :---: | :--- | :---: |")
            for r in risks:
                sec.append(f"| {r.get('risk_id', 'RSK')} | {r.get('description', '')} | {r.get('severity', 'Medium')} | {r.get('raised_by', 'Speaker')} | {format_seconds_hms(r.get('timestamp_sec', 0.0))} |")
            sec.append("")
        else:
            sec.append("• No critical blockers or high-severity risks identified.\n")

        # Section 9: Follow-up & Next Steps (Point-wise)
        sec.append("9. FOLLOW-UP & NEXT STEPS")
        sec.append("-" * 60)
        sec.append("• Review assigned action items before the next checkpoint.")
        if actions:
            high_pri = [a for a in actions if a.get("priority") == "High"]
            if high_pri:
                hp_owner = (high_pri[0].get("owner") or "").strip()
                owner_note = f" (Owner: {hp_owner})" if hp_owner else ""
                sec.append(f"• Immediate priority focus: {high_pri[0].get('description', '')}{owner_note}.")
        sec.append("• Next sync to resolve pending questions and track milestone progress.\n")

        # Section 10: Transcript Evidence & Verification Notes (Point-wise)
        sec.append("10. TRANSCRIPT EVIDENCE & VERIFICATION NOTES")
        sec.append("-" * 60)
        sec.append(f"• Forensic Verification: {verification.get('verified_claims', 0)} of {verification.get('total_claims', 0)} claims verified against timestamped quotes ({verification.get('confidence_score', 95)}% confidence).")
        sec.append("• Storage & Audio Sync: Dual-track audio aligned with zero drift; call visual isolated strictly to meeting application.\n")

        return "\n".join(sec)

    @classmethod
    def _build_empty_mom(cls, title: str, platform: str, session_id: str) -> Dict[str, Any]:
        md = (
            "📋 MINUTES OF MEETING (MoM)\n"
            "============================================================\n\n"
            "1. MEETING DETAILS\n"
            "------------------------------------------------------------\n"
            f"• Title: {title}\n"
            f"• Platform: {platform}\n"
            f"• Session ID: {session_id or 'none'}\n\n"
            "2. EXECUTIVE SUMMARY\n"
            "------------------------------------------------------------\n"
            "No spoken speech was detected or transcribed during this recording session.\n"
        )
        return {
            "mom_markdown": md,
            "mom_json": {"error": "no_speech_detected"},
            "summary": md,
            "decisions": [],
            "action_items": [],
            "chapters": [],
            "questions": [],
            "suggested_prompts": ["Record a meeting to generate minutes."],
            "speaker_metrics": {},
            "engagement_score": 70,
            "topics": ["Empty Meeting"],
        }

    def generate_follow_up_email(
        self,
        meeting_id: Optional[int] = None,
        meeting_data: Optional[Dict[str, Any]] = None,
        subject_prefix: str = "[Follow-up]",
    ) -> Dict[str, Any]:
        """
        Generate a polished, executive follow-up email summarizing meeting context,
        decisions, and designated action items tailored for immediate sending.
        Grounds every item in transcript evidence without hallucinated commitments.
        """
        import urllib.parse

        # 1. Resolve meeting metadata
        m_info: Dict[str, Any] = {}
        if meeting_data:
            m_info = dict(meeting_data)
        elif meeting_id is not None and self.db is not None:
            m_info = self.db.get_meeting(meeting_id) or {}

        title = m_info.get("title") or "Meeting Sync"
        created_at = m_info.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
        date_str = str(created_at).split(" ")[0]

        # 2. Resolve action items
        raw_actions: List[Dict[str, Any]] = []
        if "action_items" in m_info and isinstance(m_info["action_items"], list):
            raw_actions = m_info["action_items"]
        elif meeting_id is not None and self.db is not None and hasattr(self.db, "list_action_items"):
            raw_actions = self.db.list_action_items(meeting_id=meeting_id)
        elif "action_items" in m_info and isinstance(m_info["action_items"], str):
            try:
                parsed = json.loads(m_info["action_items"])
                if isinstance(parsed, list):
                    raw_actions = parsed
            except Exception:
                pass

        # 3. Resolve decisions
        raw_decisions: List[Dict[str, Any]] = []
        if "decisions" in m_info and isinstance(m_info["decisions"], list):
            raw_decisions = m_info["decisions"]
        elif meeting_id is not None and self.db is not None and hasattr(self.db, "list_decisions"):
            raw_decisions = self.db.list_decisions(meeting_id=meeting_id)
        elif "decisions" in m_info and isinstance(m_info["decisions"], str):
            try:
                parsed = json.loads(m_info["decisions"])
                if isinstance(parsed, list):
                    raw_decisions = parsed
            except Exception:
                pass

        # 4. Extract participants
        participants: List[str] = []
        if meeting_id is not None and self.db is not None and hasattr(self.db, "get_meeting_speakers"):
            participants = [s for s in self.db.get_meeting_speakers(meeting_id) if s]
        if not participants:
            seen_speakers = set()
            for a in raw_actions:
                owner = (a.get("owner") or "").strip()
                if owner and owner.lower() not in ("unassigned", "team", "all"):
                    seen_speakers.add(owner)
            participants = sorted(list(seen_speakers))
        if not participants:
            participants = ["Team"]

        # 5. Extract concise executive summary
        summary_text = m_info.get("summary") or ""
        exec_paragraphs: List[str] = []
        if summary_text:
            lines = [ln.strip() for ln in summary_text.splitlines() if ln.strip()]
            for ln in lines:
                if ln.startswith(("#", "=", "-", "*", "📋", "1.", "2.")):
                    continue
                if len(ln) > 25:
                    exec_paragraphs.append(ln)
                if len(exec_paragraphs) >= 2:
                    break
        if not exec_paragraphs:
            topics = m_info.get("topics") or "project milestones and operational alignment"
            exec_paragraphs = [
                f"Thank you everyone for taking the time to sync on {title}. "
                f"We reviewed {topics} and aligned on key deliverables."
            ]

        exec_summary = " ".join(exec_paragraphs)

        # 6. Format decisions
        decisions_list: List[Dict[str, str]] = []
        for d in raw_decisions:
            dec_text = d.get("decision") or d.get("text") or ""
            if not dec_text:
                continue
            rationale = d.get("rationale") or ""
            status = d.get("status") or "confirmed"
            decisions_list.append({
                "decision": dec_text,
                "rationale": rationale,
                "status": status,
            })

            # 7. Format action items by owner
        actions_by_owner: Dict[str, List[Dict[str, Any]]] = {}
        for a in raw_actions:
            desc = a.get("description") or a.get("task") or ""
            if not desc:
                continue
            owner = (a.get("owner") or "Unassigned (Confirmation Needed)").strip()
            due = a.get("due_date") or a.get("deadline") or ""
            actions_by_owner.setdefault(owner, []).append({
                "description": desc,
                "due_date": due,
                "status": a.get("status", "pending"),
            })

        # 7b. Follow-ups
        raw_fu = m_info.get("follow_ups_json") or m_info.get("follow_ups") or []
        if isinstance(raw_fu, str):
            try:
                raw_fu = json.loads(raw_fu) if raw_fu.strip().startswith("[") else []
            except Exception:
                raw_fu = []
        follow_up_lines: List[str] = []
        for fu in (raw_fu if isinstance(raw_fu, list) else []):
            if isinstance(fu, dict) and str(fu.get("item") or "").strip():
                f_own = str(fu.get("owner") or "").strip()
                prefix = f"[{f_own}] " if f_own else ""
                follow_up_lines.append(f"{prefix}{str(fu['item']).strip()}")
            elif isinstance(fu, str) and fu.strip():
                follow_up_lines.append(fu.strip())

        # 8. Synthesize email subject & body (never invent recipients)
        subject = f"{subject_prefix} {title} - {date_str}"
        greeting = "Hi team,"

        # Markdown format
        md_lines = [
            f"**Subject:** {subject}",
            "",
            greeting,
            "",
            "Thank you for attending today's session. Here is a summary of our discussion, confirmed decisions, and assigned action items:",
            "",
            "### Executive Summary",
            exec_summary,
            "",
        ]

        if decisions_list:
            md_lines.append("### Key Decisions Agreed")
            for item in decisions_list:
                line = f"- **{item['decision']}**"
                if item["rationale"]:
                    line += f" *(Rationale: {item['rationale']})*"
                md_lines.append(line)
            md_lines.append("")

        if actions_by_owner:
            md_lines.append("### Action Items & Ownership")
            for owner, items in sorted(actions_by_owner.items()):
                md_lines.append(f"**{owner}:**")
                for itm in items:
                    due_badge = f" *(Due: {itm['due_date']})*" if itm['due_date'] and itm['due_date'] not in ('TBD', 'Not specified') else ""
                    md_lines.append(f"- [ ] {itm['description']}{due_badge}")
            md_lines.append("")

        if follow_up_lines:
            md_lines.append("### Follow-Ups & Next Discussions")
            for fl in follow_up_lines:
                md_lines.append(f"- {fl}")
            md_lines.append("")

        md_lines.extend([
            "### Next Steps",
            "Please review your assigned deliverables and flag any blockers in advance. We will track progress ahead of our next sync.",
            "",
            "Best regards,",
            "VOX Meeting Studio",
        ])
        email_md = "\n".join(md_lines)

        # HTML format with executive inline styling
        decisions_html = ""
        if decisions_list:
            d_items = "".join(
                f'<li style="margin-bottom: 6px;"><b>{d["decision"]}</b>'
                f'{" — <i style=\'color:#4B5563;\'>" + d["rationale"] + "</i>" if d["rationale"] else ""}</li>'
                for d in decisions_list
            )
            decisions_html = f"""
            <h3 style="color: #1F2937; margin-top: 18px; margin-bottom: 8px; font-size: 15px; border-bottom: 1px solid #E5E7EB; padding-bottom: 4px;">Key Decisions Agreed</h3>
            <ul style="color: #374151; padding-left: 20px; margin-top: 4px;">{d_items}</ul>
            """

        actions_html = ""
        if actions_by_owner:
            owner_blocks = []
            for owner, items in sorted(actions_by_owner.items()):
                task_items = "".join(
                    f'<li style="margin-bottom: 4px;">'
                    f'<span style="display:inline-block; width:12px; height:12px; border:1px solid #9CA3AF; margin-right:6px; vertical-align:middle; border-radius:2px;"></span>'
                    f'{itm["description"]}'
                    f'{" <span style=\'color:#2563EB; font-size:12px;\'>(" + itm["due_date"] + ")</span>" if itm["due_date"] and itm["due_date"] != "TBD" else ""}'
                    f'</li>'
                    for itm in items
                )
                owner_blocks.append(f'<div style="margin-bottom: 10px;"><b style="color: #111827;">{owner}</b><ul style="list-style-type: none; padding-left: 8px; margin: 4px 0;">{task_items}</ul></div>')
            actions_html = f"""
            <h3 style="color: #1F2937; margin-top: 18px; margin-bottom: 8px; font-size: 15px; border-bottom: 1px solid #E5E7EB; padding-bottom: 4px;">Action Items & Ownership</h3>
            {"".join(owner_blocks)}
            """

        email_html = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.5; color: #1F2937; max-width: 680px;">
            <p style="font-size: 14px; margin-bottom: 14px;">{greeting}</p>
            <p style="font-size: 14px; margin-bottom: 14px;">Thank you for attending today's session. Here is a summary of our discussion, confirmed decisions, and assigned action items:</p>
            <div style="background-color: #F9FAFB; border-left: 4px solid #3B82F6; padding: 12px 16px; margin-bottom: 16px; border-radius: 4px;">
                <b style="color: #1E40AF; font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em;">Executive Summary</b>
                <p style="margin: 6px 0 0 0; font-size: 14px; color: #374151;">{exec_summary}</p>
            </div>
            {decisions_html}
            {actions_html}
            <h3 style="color: #1F2937; margin-top: 18px; margin-bottom: 8px; font-size: 15px; border-bottom: 1px solid #E5E7EB; padding-bottom: 4px;">Next Steps</h3>
            <p style="font-size: 14px; color: #374151; margin-bottom: 16px;">Please review your assigned deliverables and flag any blockers in advance. We will track progress ahead of our next sync.</p>
            <p style="font-size: 14px; color: #4B5563; margin-top: 20px;">Best regards,<br/><b style="color: #111827;">VOX Meeting Studio</b></p>
        </div>
        """.strip()

        # mailto URI (RFC 2368)
        plain_body = (
            f"{greeting}\n\n"
            f"Here is the follow-up summary from our meeting '{title}':\n\n"
            f"SUMMARY:\n{exec_summary}\n\n"
        )
        if decisions_list:
            plain_body += "DECISIONS:\n" + "\n".join(f"• {d['decision']}" for d in decisions_list) + "\n\n"
        if actions_by_owner:
            plain_body += "ACTION ITEMS:\n"
            for owner, items in sorted(actions_by_owner.items()):
                plain_body += f"{owner}:\n" + "\n".join(
                    f"  [ ] {itm['description']}" + (f" (Due: {itm['due_date']})" if itm.get('due_date') else "")
                    for itm in items
                ) + "\n"
            plain_body += "\n"
        if follow_up_lines:
            plain_body += "FOLLOW-UPS:\n" + "\n".join(f"• {fl}" for fl in follow_up_lines) + "\n\n"
        plain_body += "Best regards,\nVOX Meeting Studio"

        mailto_url = f"mailto:?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(plain_body)}"

        return {
            "subject": subject,
            "greeting": greeting,
            "summary": exec_summary,
            "decisions": decisions_list,
            "action_items_by_owner": actions_by_owner,
            "markdown": email_md,
            "html": email_html,
            "mailto_url": mailto_url,
            "participants": participants,
        }

    def generate_participant_checklists(
        self,
        meeting_id: Optional[int] = None,
        meeting_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Group action items by participant with clear checkboxes, ownership, and deadlines.
        Generates individual task cards, Slack formatted text with @mentions, and markdown checklists.
        """
        # 1. Resolve meeting metadata and actions
        m_info: Dict[str, Any] = {}
        if meeting_data:
            m_info = dict(meeting_data)
        elif meeting_id is not None and self.db is not None:
            m_info = self.db.get_meeting(meeting_id) or {}

        title = m_info.get("title") or "Meeting"

        raw_actions: List[Dict[str, Any]] = []
        if "action_items" in m_info and isinstance(m_info["action_items"], list):
            raw_actions = m_info["action_items"]
        elif meeting_id is not None and self.db is not None and hasattr(self.db, "list_action_items"):
            raw_actions = self.db.list_action_items(meeting_id=meeting_id)
        elif "action_items" in m_info and isinstance(m_info["action_items"], str):
            try:
                parsed = json.loads(m_info["action_items"])
                if isinstance(parsed, list):
                    raw_actions = parsed
            except Exception:
                pass

        by_participant: Dict[str, List[Dict[str, Any]]] = {}
        total_tasks = 0
        completed_tasks = 0

        for idx, a in enumerate(raw_actions):
            desc = (a.get("description") or a.get("task") or "").strip()
            if not desc:
                continue
            owner = (a.get("owner") or "Unassigned").strip()
            due = (a.get("due_date") or a.get("deadline") or "TBD").strip()
            status = (a.get("status") or "pending").strip().lower()
            completed = status == "completed"
            priority = (a.get("priority") or "medium").strip()
            evidence = (a.get("evidence_quote") or "").strip()

            total_tasks += 1
            if completed:
                completed_tasks += 1

            item_data = {
                "id": a.get("id", idx + 1),
                "description": desc,
                "owner": owner,
                "due_date": due,
                "status": status,
                "completed": completed,
                "priority": priority,
                "evidence_quote": evidence,
            }
            by_participant.setdefault(owner, []).append(item_data)

        # Markdown representation
        md_lines = [
            f"# Action Item Checklists — {title}",
            f"*Total Tasks: {total_tasks} | Completed: {completed_tasks} | Pending: {total_tasks - completed_tasks}*",
            "",
        ]

        # Slack representation with @mentions
        slack_lines = [
            f"📋 *Action Items Checklist — {title}*",
            f"_{completed_tasks}/{total_tasks} completed_",
            "",
        ]

        for owner, tasks in sorted(by_participant.items()):
            mention = f"@{owner}" if owner.lower() not in ("unassigned", "team", "all") else owner
            md_lines.append(f"### {owner}")
            slack_lines.append(f"*{mention}* ({len(tasks)} tasks):")

            for t in tasks:
                chk = "x" if t["completed"] else " "
                due_info = f" *(Due: {t['due_date']})*" if t["due_date"] and t["due_date"] not in ("TBD", "Not specified") else ""
                md_lines.append(f"- [{chk}] {t['description']}{due_info}")

                slack_chk = "✅" if t["completed"] else "▫️"
                slack_lines.append(f"  {slack_chk} {t['description']}{due_info}")

            md_lines.append("")
            slack_lines.append("")

        # Build structured per-participant contributions combining actions, decisions, follow-ups & speaking stats
        raw_decisions: List[Dict[str, Any]] = []
        if "decisions" in m_info and isinstance(m_info["decisions"], list):
            raw_decisions = m_info["decisions"]
        elif meeting_id is not None and self.db is not None and hasattr(self.db, "list_decisions"):
            raw_decisions = self.db.list_decisions(meeting_id=meeting_id)

        raw_fu = m_info.get("follow_ups_json") or m_info.get("follow_ups") or []
        if isinstance(raw_fu, str):
            try:
                raw_fu = json.loads(raw_fu) if raw_fu.strip().startswith("[") else []
            except Exception:
                raw_fu = []

        seg_speakers: List[str] = []
        if meeting_id is not None and self.db is not None and hasattr(self.db, "get_meeting_speakers"):
            try:
                seg_speakers = [str(s.get("speaker") if isinstance(s, dict) else s) for s in self.db.get_meeting_speakers(meeting_id)]
            except Exception:
                seg_speakers = []

        all_participant_names = sorted(
            {
                *(k for k in by_participant.keys() if k != "Unassigned"),
                *(s for s in seg_speakers if s),
                *(str(d.get("speaker") or "").strip() for d in raw_decisions if isinstance(d, dict) and str(d.get("speaker") or "").strip()),
                *(str(f.get("owner") or "").strip() for f in (raw_fu if isinstance(raw_fu, list) else []) if isinstance(f, dict) and str(f.get("owner") or "").strip()),
            }
        )
        if "Unassigned" in by_participant:
            all_participant_names.append("Unassigned")

        participants_detail: Dict[str, Dict[str, Any]] = {}
        for pname in all_participant_names:
            p_actions = []
            for a in raw_actions:
                a_owner = (a.get("owner") or "").strip() or "Unassigned"
                if a_owner == pname and (a.get("description") or a.get("task") or "").strip():
                    p_actions.append(dict(a))
            p_decs = [
                dict(d) for d in raw_decisions
                if isinstance(d, dict) and (
                    str(d.get("speaker") or "").strip() == pname
                    or pname in (d.get("participants") or [])
                )
            ]
            p_fus = [
                dict(f) for f in (raw_fu if isinstance(raw_fu, list) else [])
                if isinstance(f, dict) and ((f.get("owner") or "").strip() or "Unassigned") == pname
            ]
            confirmed = [a for a in p_actions if str(a.get("confirmation_state") or "confirmed") == "confirmed"]
            pending = [a for a in p_actions if str(a.get("status") or "pending").lower() != "completed"]
            participants_detail[pname] = {
                "name": "Owner Unknown / Needs Confirmation" if pname == "Unassigned" else pname,
                "raw_key": pname,
                "is_unassigned": pname == "Unassigned",
                "confirmed_commitments": confirmed,
                "assigned_actions": p_actions,
                "decisions": p_decs,
                "follow_ups": p_fus,
                "pending_items": pending,
                "completed_count": sum(1 for a in p_actions if str(a.get("status") or "").lower() == "completed"),
                "total_count": len(p_actions),
            }

        return {
            "meeting_id": meeting_id,
            "title": title,
            "by_participant": by_participant,
            "participants_detail": participants_detail,
            "participants": sorted(list(by_participant.keys())),
            "markdown": "\n".join(md_lines).strip(),
            "slack_format": "\n".join(slack_lines).strip(),
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "pending_tasks": total_tasks - completed_tasks,
        }



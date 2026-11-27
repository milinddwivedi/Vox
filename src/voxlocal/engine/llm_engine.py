"""
VOX Local LLM Engine — 100% Offline Meeting Intelligence & Knowledge Extraction.

Powered by `Qwen3.5-4B-Q4_K_M.gguf` served locally via isolated `llama-server.exe`
(Vulkan GPU acceleration by default with explicit, non-silent CPU fallback).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from voxlocal.core.logger import get_app_root, get_logger, get_user_models_root
from voxlocal.engine.llama_server_engine import LlamaServerManager, _strip_think_blocks

logger = get_logger("llm_engine")


def _compact_transcript_lines(segments: List[Dict[str, Any]], max_words: int = 12500) -> str:
    """Format segments compactly as `[MM:SS|Speaker] text` fitting inside 24k context."""
    lines: List[str] = []
    for s in segments or []:
        txt = str(s.get("text") or "").strip()
        if not txt:
            continue
        st = max(0, int(float(s.get("start_time") or 0.0)))
        mm, ss = divmod(st, 60)
        spk = str(s.get("speaker") or "Speaker").strip()
        lines.append(f"[{mm:02d}:{ss:02d}|{spk}] {txt}")

    if not lines:
        return ""

    full = "\n".join(lines)
    words = full.split()
    if len(words) <= max_words:
        return full

    # Stratified head + cue-preserving middle + tail for > 2-hour transcripts
    n = len(lines)
    h_end = max(1, int(n * 0.28))
    t_start = min(n - 1, int(n * 0.72))
    cue_pat = re.compile(
        r"\b(decided|agreed|approved|confirmed|will|action|deadline|follow up|risk|blocker|issue|\?)\b",
        re.IGNORECASE,
    )
    mid = [
        ln for i, ln in enumerate(lines[h_end:t_start], h_end)
        if cue_pat.search(ln) or (i % 2 == 0)
    ]
    head_lines = lines[:h_end]
    tail_lines = lines[t_start:]
    fixed_words = len("\n".join(head_lines).split()) + len("\n".join(tail_lines).split()) + 8
    rem_budget = max(0, max_words - fixed_words)
    kept_mid: List[str] = []
    mid_words = 0
    for ln in mid:
        w = len(ln.split())
        if mid_words + w > rem_budget:
            break
        kept_mid.append(ln)
        mid_words += w

    combined = (
        "\n".join(head_lines)
        + "\n\n[... middle discussion ...]\n\n"
        + ("\n".join(kept_mid) if kept_mid else "[omitted to fit context window]")
        + "\n\n[... concluding section ...]\n\n"
        + "\n".join(tail_lines)
    )
    c_words = combined.split()
    if len(c_words) > max_words:
        # Preserve newlines while trimming excess lines proportionally from head/tail if extreme
        trimmed: List[str] = []
        w_cnt = 0
        for ln in combined.splitlines():
            lw = len(ln.split())
            if w_cnt + lw > max_words and trimmed:
                break
            trimmed.append(ln)
            w_cnt += lw
        combined = "\n".join(trimmed)
    return combined


_SHARED_MEETING_SYSTEM_PROMPT = (
    "You are VOX Local Meeting Intelligence. Respond strictly with valid JSON grounded ONLY in the provided meeting transcript.\n"
    "Never invent people, metrics, deadlines, owners, or facts not explicitly stated in the transcript.\n"
    "Distinguish real commitments/decisions from casual greetings, farewells, holiday wishes, small talk, or generic walkthroughs.\n"
    "Use exact [MM:SS] timestamps when citing moments."
)

_GREETING_FAREWELL_RE = re.compile(
    r"^(?:review and address:\s*)?(?:all\s+right,?\s*|okay,?\s*)?(?:hi|hello|hey|good morning|good afternoon|good evening|"
    r"bye|goodbye|see you|have a (?:good|great|wonderful|happy) (?:holiday|weekend|day|evening)|"
    r"take care|be safe|thanks? everyone|thank you|appreciate your work)\b",
    re.IGNORECASE,
)

_NULL_FIELD_TOKENS = {
    "",
    "none",
    "null",
    "n/a",
    "na",
    "unknown",
    "unassigned",
    "unassigned — confirmation required",
    "owner unknown",
    "not specified",
    "no due date",
    "tbd",
}


class LocalLLMEngine:
    """
    Production Local LLM Engine wrapping Qwen3.5-4B-Q4_K_M via LlamaServerManager.
    Features:
    - Lazy loopback server startup (zero cold-start penalty)
    - Vulkan GPU default with automatic CPU fallback and explicit fallback reason
    - Two-call Meeting Intelligence extraction (Summary/Detailed/Points/Follow-ups + Decisions/Action Items)
    - Strict null preservation (`owner=None`, `due_date=None` when unstated)
    - Structured Knowledge Extraction (`extract_meeting_knowledge`)
    - Grounded single-meeting & cross-meeting Ask VOX Q&A with absent-fact refusal
    """

    DEFAULT_MODEL = "Qwen3.5-4B-Q4_K_M.gguf"

    def __init__(
        self,
        model_dir: Optional[str | Path] = None,
        device: str = "auto",
        compute_type: str = "q4_k_m",
        intra_threads: int = 8,
        idle_timeout_sec: int = 300,
    ) -> None:
        if model_dir is None:
            target_path = get_user_models_root() / "meeting_llm" / self.DEFAULT_MODEL
        else:
            p = Path(model_dir)
            target_path = (p / self.DEFAULT_MODEL) if p.is_dir() else p
            # Seamless fallback if legacy CT2 directory path was passed
            if not target_path.exists() and "Llama-3.2-1B" in str(target_path):
                cand = get_user_models_root() / "meeting_llm" / self.DEFAULT_MODEL
                if cand.exists():
                    target_path = cand

        self.model_path = target_path
        self.model_dir = target_path.parent
        self.requested_device = str(device or "auto")
        self.compute_type = compute_type
        self.intra_threads = intra_threads
        self._server = LlamaServerManager(
            model_path=target_path,
            device=self.requested_device,
            ctx_size=24576,
            idle_timeout_sec=idle_timeout_sec,
        )
        self._last_reliability_telemetry: Dict[str, Any] = {}
        self._last_usage_log: List[Dict[str, Any]] = []

    def get_last_reliability_telemetry(self) -> Dict[str, Any]:
        return dict(self._last_reliability_telemetry)

    def get_last_usage_log(self) -> List[Dict[str, Any]]:
        return list(self._last_usage_log)

    def _chat_json_with_retry(
        self,
        *,
        call_name: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
        required_keys: Tuple[str, ...],
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """Execute a structured JSON call with raw-vs-repaired tracking and one clean retry on parse miss."""
        tel: Dict[str, Any] = {
            "call": call_name,
            "attempts": 0,
            "raw_json_valid": False,
            "post_repair_valid": False,
            "repaired_used": False,
            "thinking_disabled_verified": None,
        }
        for attempt in (1, 2):
            tel["attempts"] = attempt
            eff_msgs = list(messages)
            if attempt == 2:
                eff_msgs = list(messages[:-1]) + [
                    {
                        "role": "user",
                        "content": messages[-1]["content"]
                        + "\n\nIMPORTANT: Output ONLY a single compact, valid JSON object matching the schema with all brackets closed.",
                    }
                ]
            raw = self._server.chat(
                eff_msgs,
                max_tokens=max_tokens if attempt == 1 else min(max_tokens + 250, 2200),
                temperature=temperature if attempt == 1 else 0.0,
                json_mode=True,
            )
            usage = self._server.get_last_usage() if hasattr(self._server, "get_last_usage") else {}
            if usage:
                usage_rec = {"call": call_name, "attempt": attempt, **usage}
                self._last_usage_log.append(usage_rec)
                tel["thinking_disabled_verified"] = usage.get("thinking_disabled_verified")
                tel["completion_tokens"] = usage.get("completion_tokens")
                tel["prompt_tokens"] = usage.get("prompt_tokens")
            if not raw:
                continue
            raw_clean = _strip_think_blocks(raw).strip()
            try:
                direct = json.loads(raw_clean)
                if isinstance(direct, dict) and any(k in direct for k in required_keys):
                    tel["raw_json_valid"] = True
                    tel["post_repair_valid"] = True
                    return self._sanitize_semantic_mom(direct), tel
            except Exception:
                pass
            parsed = self._parse_json_safely(raw_clean)
            if isinstance(parsed, dict) and any(k in parsed for k in required_keys):
                tel["post_repair_valid"] = True
                tel["repaired_used"] = True
                return parsed, tel
        return None, tel

    @staticmethod
    def _get_free_vram_mb() -> Optional[int]:
        return LlamaServerManager._get_free_vram_mb()

    def is_available(self) -> bool:
        return self._server.is_available()

    def get_model_info(self) -> Dict[str, Any]:
        info = self._server.get_info()
        info["compute_type"] = self.compute_type
        info["threads"] = self.intra_threads
        return info

    def unload(self) -> None:
        self._server.stop()

    def shutdown(self) -> None:
        self._server.shutdown()

    def generate(
        self,
        prompt: str,
        max_tokens: int = 1200,
        temperature: float = 0.1,
        top_p: float = 0.9,
        stop_tokens: Optional[List[str]] = None,
        json_mode: bool = False,
    ) -> Optional[str]:
        return self._server.chat(
            [{"role": "user", "content": str(prompt or "")}],
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
        )

    def generate_stream(
        self,
        prompt: str,
        max_tokens: int = 700,
        temperature: float = 0.15,
        top_p: float = 0.9,
        stop_tokens: Optional[List[str]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Iterator[str]:
        return self._server.chat_stream(
            [{"role": "user", "content": str(prompt or "")}],
            max_tokens=max_tokens,
            temperature=temperature,
            cancel_check=cancel_check,
        )

    def generate_meeting_analysis(
        self,
        segments: List[Dict[str, Any]],
        title: str = "Meeting Notes",
        platform: str = "Zoom",
        duration_sec: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Two-call grounded meeting intelligence extraction:
        Call 1: Overview (purpose/outcome), Brief & Detailed Summaries, Key Takeaways, Topics, Follow-ups, Risks, Open Questions
        Call 2: Confirmed Decisions & Actionable Tasks with strict commitment filtering & null preservation
        """
        if not self.is_available():
            return None
        self._server.ensure_started()
        ctx_now = int(getattr(self._server, "active_ctx_size", 16384) or 16384)
        is_cpu = self._server.active_device == "cpu"
        max_words = 3200 if ctx_now <= 8192 else (5800 if (is_cpu or ctx_now <= 16384) else 10500)
        call_max_tokens = 1200 if ctx_now <= 8192 else (1600 if is_cpu else 2000)
        transcript = _compact_transcript_lines(segments, max_words=max_words)
        if not transcript:
            return None

        dur_min, dur_rem = divmod(max(0, int(duration_sec)), 60)
        dur_str = f"{dur_min}m {dur_rem}s" if dur_min > 0 else f"{dur_rem}s"

        base_prefix = f"Meeting: {title}\n\nTRANSCRIPT:\n{transcript}"

        task1_summary = (
            f"TASK:\nExtract structured minutes for this {platform} meeting (Duration: {dur_str}).\n"
            "Rules:\n"
            "- Write brief_summary so it can be read in 30 seconds (2-4 sharp factual sentences).\n"
            "- Write detailed_summary as well-organized sections covering context, objectives, key topics, agreements, disagreements, dependencies, and next steps — NEVER repeat transcript lines verbatim.\n"
            "- Populate follow_ups with pending confirmations, future discussion topics, dependencies, or promised follow-ups explicitly mentioned (e.g. sharing files later, checking back, revisiting next week).\n"
            "- Keep key_discussion_points <= 7, topics <= 6, follow_ups <= 7, open_questions <= 6, risks <= 5.\n"
            "Schema:\n"
            "{\n"
            '  "meeting_purpose": "One sentence stating the primary purpose of this meeting.",\n'
            '  "overall_outcome": "One sentence stating the main outcome achieved.",\n'
            '  "brief_summary": "Concise 30-second executive brief covering purpose, outcomes, critical decisions/actions, and blockers.",\n'
            '  "detailed_summary": "Comprehensive structured multi-paragraph synthesis with clear plain headings covering objectives, topic-by-topic clarifications, agreements, dependencies, and next steps.",\n'
            '  "key_discussion_points": [\n'
            '    {"topic": "Short topic", "summary": "Specific factual takeaway", "timestamp_str": "MM:SS", "speakers": ["Speaker"]}\n'
            "  ],\n"
            '  "topics": [{"title": "Topic title", "summary": "Concise topic summary", "details": ["Specific factual detail"], "participants": ["Speaker"], "timestamp_str": "MM:SS"}],\n'
            '  "follow_ups": [{"item": "Concrete follow-up item", "category": "pending_confirmation|question|future_discussion|dependency|promised_followup", "owner": null, "timestamp_str": "MM:SS", "evidence_quote": "Short quote"}],\n'
            '  "open_questions": [{"question": "Unresolved question", "asked_by": "Speaker", "context": "Short context", "timestamp_str": "MM:SS"}],\n'
            '  "risks": [{"type": "risk|blocker", "risk": "Risk or blocker mentioned", "impact": "Stated consequence", "mitigation": "Mentioned mitigation or None", "timestamp_str": "MM:SS", "evidence_quote": "Short quote"}]\n'
            "}"
        )

        summary_data, tel1 = self._chat_json_with_retry(
            call_name="call1_summary",
            messages=[
                {"role": "system", "content": _SHARED_MEETING_SYSTEM_PROMPT},
                {"role": "user", "content": f"{base_prefix}\n\n{task1_summary}"},
            ],
            max_tokens=call_max_tokens,
            temperature=0.1,
            required_keys=("brief_summary", "executive_summary", "detailed_summary", "topics", "key_discussion_points"),
        )

        task2_actions = (
            "TASK:\nExtract ONLY genuine decisions and genuine actionable commitments/next steps from the transcript above.\n"
            "CRITICAL RULES:\n"
            "1. STRICT FILTERING: NEVER include greetings, farewells, holiday wishes (e.g. 'have a good holiday', 'be safe on the road'), small talk, or casual explanations without a clear commitment or decision.\n"
            "2. Each action_items description MUST be written as a concise verb-led task (max 22 words), NOT a copied conversational quote.\n"
            "3. Set \"owner\": null whenever a single specific participant/speaker name is NOT explicitly committed. NEVER guess or write 'Unassigned'.\n"
            "4. Set \"due_date\": null whenever a calendar date or timeframe (e.g. Friday, tomorrow, next week) is NOT explicitly spoken. NEVER write 'Not specified' or invent dates.\n"
            "5. Set \"priority\": \"High\"|\"Medium\"|\"Low\" ONLY when urgency/priority is supported; otherwise null.\n"
            "6. Extract up to 10 real decisions and up to 14 real action_items; keep verbatim evidence_quote <= 22 words with MM:SS timestamp_str.\n"
            "Respond strictly with valid JSON matching:\n"
            "{\n"
            '  "decisions": [\n'
            '    {"decision": "Clear decision statement", "category": "Strategic|Technical|Product|Budget|Process|General", "status": "confirmed|proposed|rejected|unresolved", "evolution_relationship": "new|repeated|superseded|reversed", "context": "Why this came up", "rationale": "Reasoning or context", "speaker": "ExactSpeaker", "participants": ["ExactSpeaker"], "evidence_quote": "Short verbatim quote", "timestamp_str": "MM:SS"}\n'
            "  ],\n"
            '  "action_items": [\n'
            '    {"description": "Concise verb-led action", "owner": null, "due_date": null, "priority": null, "commitment_type": "confirmed_commitment|suggested_action|needs_confirmation", "related_topic": "Short topic", "context": "Brief context", "evidence_quote": "Short verbatim quote", "timestamp_str": "MM:SS"}\n'
            "  ]\n"
            "}"
        )
        actions_data, tel2 = self._chat_json_with_retry(
            call_name="call2_decisions_actions",
            messages=[
                {"role": "system", "content": _SHARED_MEETING_SYSTEM_PROMPT},
                {"role": "user", "content": f"{base_prefix}\n\n{task2_actions}"},
            ],
            max_tokens=call_max_tokens,
            temperature=0.05,
            required_keys=("decisions", "action_items"),
        )

        self._last_reliability_telemetry = {
            "call1_summary": tel1,
            "call2_decisions_actions": tel2,
        }

        if not summary_data and not actions_data:
            return None

        merged: Dict[str, Any] = dict(summary_data or {})
        if not merged.get("executive_summary") and merged.get("brief_summary"):
            merged["executive_summary"] = merged["brief_summary"]
        if not merged.get("brief_summary") and merged.get("executive_summary"):
            merged["brief_summary"] = merged["executive_summary"]
        if actions_data:
            merged["decisions"] = actions_data.get("decisions", [])
            merged["action_items"] = actions_data.get("action_items", [])
            merged["call2_ok"] = True
        else:
            merged["call2_ok"] = False
        merged["llm_model"] = self.model_path.name
        merged["llm_device"] = self._server.active_device
        merged["llm_fallback_reason"] = self._server.fallback_reason
        merged["llm_reliability"] = dict(self._last_reliability_telemetry)
        return self._sanitize_semantic_mom(merged)

    def extract_meeting_knowledge(
        self,
        segments: List[Dict[str, Any]],
        title: str = "Meeting Notes",
        meeting_date: str = "",
    ) -> List[Dict[str, Any]]:
        """Extract typed reusable durable knowledge items while filtering out transient chatter."""
        if not self.is_available():
            return []
        self._server.ensure_started()
        ctx_now = int(getattr(self._server, "active_ctx_size", 16384) or 16384)
        is_cpu = self._server.active_device == "cpu"
        max_words = 3200 if ctx_now <= 8192 else (5800 if (is_cpu or ctx_now <= 16384) else 10500)
        call_max_tokens = 1400 if ctx_now <= 8192 else (1800 if is_cpu else 2048)
        transcript = _compact_transcript_lines(segments, max_words=max_words)
        if not transcript:
            return []

        base_prefix = f"Meeting: {title}\n\nTRANSCRIPT:\n{transcript}"
        date_info = f" (Meeting date: {meeting_date})" if meeting_date else ""
        task3_knowledge = (
            f"TASK:\nExtract durable, reusable knowledge items from the meeting transcript above{date_info}.\n"
            "Allowed item_type values ONLY: entity, acronym, glossary, participant, project, fact, decision, open_issue.\n"
            "Allowed subcategory values ONLY: person, company, project, system, acronym, terminology, process, workflow, decision, policy, fact, context.\n"
            "Rules:\n"
            "- Extract 8 to 18 high-value durable facts (team roles/ownership, client requirements, confirmed workflows, systems/ERPs, acronyms defined or used, architectural constraints, persistent facts).\n"
            "- REJECT greetings, farewells, holiday chatter, speculation, and raw conversational fragments.\n"
            "- Never invent expansions for acronyms if not defined or clearly established in the transcript.\n"
            "- Keep canonical_key lowercase concise identifier; entity_name optional related organization/system; evidence_quote <= 22 words.\n"
            "Respond strictly with JSON:\n"
            "{\n"
            '  "knowledge_items": [\n'
            '    {"item_type": "entity|acronym|glossary|participant|project|fact|decision|open_issue", "subcategory": "person|company|project|system|acronym|terminology|process|workflow|decision|policy|fact|context", "canonical_key": "short key", "title": "Display Title", "entity_name": "", "content": "Concise grounded description useful in future meetings", "aliases": [], "speaker": "ExactSpeaker", "timestamp_str": "MM:SS", "evidence_quote": "Short verbatim excerpt"}\n'
            "  ]\n"
            "}"
        )
        parsed, tel3 = self._chat_json_with_retry(
            call_name="call3_knowledge",
            messages=[
                {"role": "system", "content": _SHARED_MEETING_SYSTEM_PROMPT},
                {"role": "user", "content": f"{base_prefix}\n\n{task3_knowledge}"},
            ],
            max_tokens=call_max_tokens,
            temperature=0.05,
            required_keys=("knowledge_items",),
        )
        self._last_reliability_telemetry["call3_knowledge"] = tel3
        if not parsed:
            return []
        items = parsed.get("knowledge_items") or []
        out: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            ttl = str(it.get("title") or "").strip()
            cnt = str(it.get("content") or "").strip()
            if not ttl or not cnt:
                continue
            if _GREETING_FAREWELL_RE.search(ttl) or _GREETING_FAREWELL_RE.search(cnt):
                continue
            out.append(it)
        return out

    @staticmethod
    def _build_ask_messages(
        question: str,
        citations: List[Dict[str, Any]],
        context_meeting_title: Optional[str] = None,
        knowledge_items: Optional[List[Dict[str, Any]]] = None,
        max_citations: int = 12,
        conversation_history: Optional[List[Tuple[str, str]]] = None,
        answer_depth: str = "detailed",
    ) -> List[Dict[str, str]]:
        excerpts: List[str] = []
        for i, c in enumerate(citations[:max(1, int(max_citations))], 1):
            m_title = c.get("meeting_title") or "Meeting"
            m_date = str(c.get("meeting_date") or "")[:10]
            date_tag = f" {m_date}" if m_date else ""
            ts = c.get("timestamp_formatted")
            if not ts:
                st = int(float(c.get("timestamp_sec") or 0.0))
                mm, ss = divmod(max(0, st), 60)
                ts = f"{mm:02d}:{ss:02d}"
            spks_list = c.get("speakers")
            if isinstance(spks_list, list) and spks_list:
                spk_display = " / ".join(str(s) for s in spks_list if str(s).strip())
            else:
                spk_display = str(c.get("speaker") or "Speaker").replace(", ", " / ")
            txt = c.get("quote") or c.get("text") or ""
            excerpts.append(f"[{i}] ({m_title}{date_tag} @ {ts} | Speakers in excerpt: {spk_display}):\n{txt}")

        k_block = ""
        if knowledge_items:
            k_lines = [
                f"- [{k.get('item_type', 'fact')}] {k.get('title', '')}: {k.get('content', '')}"
                for k in knowledge_items[:6]
            ]
            k_block = "\n\nVERIFIED KNOWLEDGE MEMORY:\n" + "\n".join(k_lines)

        hist_block = ""
        if conversation_history:
            hist_lines: List[str] = []
            for hq, ha in list(conversation_history)[-4:]:
                hq_s = str(hq or "").strip()
                ha_s = str(ha or "").strip()[:320]
                if hq_s and ha_s:
                    hist_lines.append(f"User: {hq_s}\nAssistant: {ha_s}")
            if hist_lines:
                hist_block = "\n\nPRIOR CONVERSATION IN THIS SCOPE:\n" + "\n---\n".join(hist_lines)

        excerpts_text = "\n\n".join(excerpts)
        scope_info = f" (scoped ONLY to '{context_meeting_title}')" if context_meeting_title else " (across all matching meetings)"
        depth_rule = (
            "6. Keep the answer crisp and concise (3-5 bullet points max) unless multiple citations are needed."
            if str(answer_depth or "detailed").lower() == "concise"
            else "6. Structure helpful answers clearly with **Facts Supported by Sources** (and **Unknowns / Not Discussed** when part of the question is missing)."
        )

        sys_msg = (
            "You are VOX, a strictly grounded offline meeting intelligence assistant.\n"
            "Rules:\n"
            "1. Answer ONLY from the provided Meeting Excerpts and Verified Knowledge Memory.\n"
            "2. Always cite meeting title, exact speaker name, and exact timestamp `[MM:SS]` for factual claims.\n"
            "3. CRITICAL SPEAKER ATTRIBUTION: Each transcript line is tagged `[MM:SS|ExactSpeaker]`. Attribute every statement strictly to the ExactSpeaker tag on that line. NEVER merge two different speaker names into one person.\n"
            "4. CRITICAL REFUSAL RULE: If the answer or requested detail (number, budget, date, decision, owner, topic) is NOT present in the provided excerpts, state explicitly: 'This was not discussed in the provided meeting excerpts.' NEVER guess or extrapolate.\n"
            "5. If excerpts span multiple meetings, distinguish each meeting clearly.\n"
            f"{depth_rule}"
        )
        usr_msg = (
            f"MEETING EXCERPTS:\n{excerpts_text}{k_block}{hist_block}\n\n"
            f"QUESTION{scope_info}: {question}"
        )
        return [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": usr_msg},
        ]

    def answer_query(
        self,
        question: str,
        citations: List[Dict[str, Any]],
        context_meeting_title: Optional[str] = None,
        knowledge_items: Optional[List[Dict[str, Any]]] = None,
        conversation_history: Optional[List[Tuple[str, str]]] = None,
        answer_depth: str = "detailed",
    ) -> Optional[str]:
        if not citations and not knowledge_items:
            return None
        self._server.ensure_started()
        ctx_now = int(getattr(self._server, "active_ctx_size", 16384) or 16384)
        max_cit = 5 if ctx_now <= 8192 else (8 if self._server.active_device == "cpu" else 12)
        msgs = self._build_ask_messages(
            question=question,
            citations=citations,
            context_meeting_title=context_meeting_title,
            knowledge_items=knowledge_items,
            max_citations=max_cit,
            conversation_history=conversation_history,
            answer_depth=answer_depth,
        )
        return self._server.chat(msgs, max_tokens=900, temperature=0.1)

    def answer_query_stream(
        self,
        question: str,
        citations: List[Dict[str, Any]],
        context_meeting_title: Optional[str] = None,
        knowledge_items: Optional[List[Dict[str, Any]]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        conversation_history: Optional[List[Tuple[str, str]]] = None,
        answer_depth: str = "detailed",
    ) -> Iterator[str]:
        if not citations and not knowledge_items:
            return
        self._server.ensure_started()
        ctx_now = int(getattr(self._server, "active_ctx_size", 16384) or 16384)
        max_cit = 5 if ctx_now <= 8192 else (8 if self._server.active_device == "cpu" else 12)
        msgs = self._build_ask_messages(
            question=question,
            citations=citations,
            context_meeting_title=context_meeting_title,
            knowledge_items=knowledge_items,
            max_citations=max_cit,
            conversation_history=conversation_history,
            answer_depth=answer_depth,
        )
        for chunk in self._server.chat_stream(
            msgs, max_tokens=900, temperature=0.1, cancel_check=cancel_check
        ):
            yield chunk

    @staticmethod
    def _parse_json_safely(raw_text: str) -> Optional[Dict[str, Any]]:
        """Extract and parse JSON object defensively with truncation repair."""
        text = _strip_think_blocks(raw_text)
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        start_idx = text.find("{")
        if start_idx == -1:
            return None

        candidates: List[str] = [text[start_idx:]]
        end_idx = text.rfind("}")
        if end_idx != -1 and end_idx > start_idx and (end_idx + 1) < len(text):
            candidates.append(text[start_idx : end_idx + 1])

        for cand in candidates:
            try:
                data = json.loads(cand)
                if isinstance(data, dict):
                    return LocalLLMEngine._sanitize_semantic_mom(data)
            except json.JSONDecodeError:
                pass

            cleaned = re.sub(r",\s*([\]}])", r"\1", cand)
            try:
                data = json.loads(cleaned)
                if isinstance(data, dict):
                    return LocalLLMEngine._sanitize_semantic_mom(data)
            except Exception:
                pass

            repaired = LocalLLMEngine._repair_truncated_json(cleaned)
            if repaired:
                try:
                    data = json.loads(repaired)
                    if isinstance(data, dict):
                        return LocalLLMEngine._sanitize_semantic_mom(data)
                except Exception:
                    pass

        return None

    @staticmethod
    def _sanitize_semantic_mom(data: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize extracted MoM fields, filter farewell/chatter noise, and keep unstated fields as None."""
        if not isinstance(data, dict):
            return data

        if "action_items" in data and isinstance(data["action_items"], list):
            cleaned_actions = []
            seen_act: set[str] = set()
            for a in data["action_items"]:
                if isinstance(a, dict) and str(a.get("description") or "").strip():
                    desc = re.sub(r"^review and address:\s*", "", str(a["description"]).strip(), flags=re.IGNORECASE).strip()
                    if not desc or _GREETING_FAREWELL_RE.search(desc):
                        continue
                    norm_k = " ".join(desc.lower().split())
                    if norm_k in seen_act:
                        continue
                    seen_act.add(norm_k)
                    a["description"] = desc

                    raw_owner = a.get("owner")
                    owner_s = str(raw_owner).strip() if raw_owner is not None else ""
                    a["owner"] = None if owner_s.lower() in _NULL_FIELD_TOKENS else owner_s

                    raw_due = a.get("due_date")
                    due_s = str(raw_due).strip() if raw_due is not None else ""
                    a["due_date"] = None if due_s.lower() in _NULL_FIELD_TOKENS else due_s

                    raw_prio = a.get("priority")
                    prio_s = str(raw_prio).strip().capitalize() if raw_prio is not None else ""
                    a["priority"] = prio_s if prio_s in ("High", "Medium", "Low") else None

                    ctype = str(a.get("commitment_type") or ("confirmed_commitment" if a["owner"] else "suggested_action")).strip().lower()
                    if ctype not in ("confirmed_commitment", "suggested_action", "needs_confirmation"):
                        ctype = "confirmed_commitment" if a["owner"] else "needs_confirmation"
                    a["commitment_type"] = ctype
                    cleaned_actions.append(a)
            data["action_items"] = cleaned_actions

        if "decisions" in data and isinstance(data["decisions"], list):
            cleaned_decisions = []
            seen_dec: set[str] = set()
            valid_cats = ("Strategic", "Technical", "Product", "Budget", "Process", "General")
            for d in data["decisions"]:
                if isinstance(d, dict) and str(d.get("decision") or "").strip():
                    dec_txt = str(d["decision"]).strip()
                    if _GREETING_FAREWELL_RE.search(dec_txt):
                        continue
                    ev_txt = str(d.get("evidence_quote") or "").strip()
                    if ev_txt and _GREETING_FAREWELL_RE.search(ev_txt):
                        continue
                    norm_d = " ".join(dec_txt.lower().split())
                    if norm_d in seen_dec:
                        continue
                    seen_dec.add(norm_d)
                    d["decision"] = dec_txt
                    cat = str(d.get("category") or "General").strip().capitalize()
                    d["category"] = cat if cat in valid_cats else "General"
                    stat = str(d.get("status") or "confirmed").strip().lower()
                    d["status"] = stat if stat in ("proposed", "confirmed", "rejected", "unresolved", "unclassified") else "confirmed"
                    evo = str(d.get("evolution_relationship") or "new").strip().lower()
                    d["evolution_relationship"] = evo if evo in ("new", "repeated", "superseded", "reversed") else "new"
                    cleaned_decisions.append(d)
            data["decisions"] = cleaned_decisions

        if "key_discussion_points" in data and isinstance(data["key_discussion_points"], list):
            data["key_discussion_points"] = [
                k for k in data["key_discussion_points"]
                if isinstance(k, dict) and (str(k.get("topic") or "").strip() or str(k.get("summary") or "").strip())
            ]

        if "topics" in data and isinstance(data["topics"], list):
            data["topics"] = [
                t for t in data["topics"]
                if isinstance(t, dict) and (str(t.get("title") or "").strip() or str(t.get("summary") or "").strip())
            ]

        if "follow_ups" in data and isinstance(data["follow_ups"], list):
            cleaned_fu = []
            for f in data["follow_ups"]:
                if isinstance(f, dict) and str(f.get("item") or "").strip():
                    raw_own = f.get("owner")
                    own_s = str(raw_own).strip() if raw_own is not None else ""
                    f["owner"] = None if own_s.lower() in _NULL_FIELD_TOKENS else own_s
                    cleaned_fu.append(f)
            data["follow_ups"] = cleaned_fu

        if "open_questions" in data and isinstance(data["open_questions"], list):
            data["open_questions"] = [
                q for q in data["open_questions"]
                if isinstance(q, dict) and str(q.get("question") or "").strip()
            ]

        if "risks" in data and isinstance(data["risks"], list):
            data["risks"] = [
                r for r in data["risks"]
                if isinstance(r, dict) and str(r.get("risk") or "").strip()
            ]

        return data

    @staticmethod
    def _repair_truncated_json(text: str) -> Optional[str]:
        in_string = False
        escape = False
        stack: List[str] = []
        cleaned_chars: List[str] = []

        for ch in text:
            if escape:
                escape = False
                cleaned_chars.append(ch)
                continue
            if ch == "\\":
                escape = True
                cleaned_chars.append(ch)
                continue
            if ch == '"':
                in_string = not in_string
                cleaned_chars.append(ch)
                continue
            if not in_string:
                if ch in ("{", "["):
                    stack.append(ch)
                elif ch == "}" and stack and stack[-1] == "{":
                    stack.pop()
                elif ch == "]" and stack and stack[-1] == "[":
                    stack.pop()
            cleaned_chars.append(ch)

        if in_string:
            cleaned_chars.append('"')

        reconstructed = "".join(cleaned_chars).rstrip()
        reconstructed = re.sub(r',\s*"[^"]*"\s*:\s*$', "", reconstructed)
        reconstructed = re.sub(r'\{\s*"[^"]*"\s*:\s*$', "{", reconstructed)
        reconstructed = re.sub(r',\s*"[^"]*"\s*$', "", reconstructed)
        reconstructed = re.sub(r",\s*$", "", reconstructed)

        while stack:
            top = stack.pop()
            reconstructed += "}" if top == "{" else "]"

        return reconstructed

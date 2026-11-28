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
from voxlocal.engine.intelligence_verifier import (
    CALL1A_SUMMARY_SCHEMA,
    CALL1B_RISKS_QUESTIONS_SCHEMA,
    CALL2_DECISIONS_ACTIONS_SCHEMA,
    CALL3_KNOWLEDGE_SCHEMA,
    verify_and_normalize_intelligence,
)
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
        json_schema: Optional[Dict[str, Any]] = None,
        schema_name: str = "structured_output",
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """Execute a schema-constrained JSON call with raw-vs-repaired tracking and clean retry."""
        tel: Dict[str, Any] = {
            "call": call_name,
            "attempts": 0,
            "raw_json_valid": False,
            "post_repair_valid": False,
            "repaired_used": False,
            "constrained_decoding_used": False,
            "schema_fallback_reason": "",
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
            try:
                raw = self._server.chat(
                    eff_msgs,
                    max_tokens=max_tokens if attempt == 1 else min(max_tokens + 300, 2200),
                    temperature=temperature if attempt == 1 else 0.0,
                    json_mode=True,
                    json_schema=json_schema,
                    schema_name=schema_name,
                )
            except TypeError:
                raw = self._server.chat(
                    eff_msgs,
                    max_tokens=max_tokens if attempt == 1 else min(max_tokens + 300, 2200),
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
                tel["constrained_decoding_used"] = bool(usage.get("constrained_decoding_used", False))
                tel["schema_fallback_reason"] = str(usage.get("schema_fallback_reason") or "")
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

    def cancel_active_stream(self) -> None:
        """Abort any active Ask VOX token stream immediately."""
        if hasattr(self._server, "cancel_active_stream"):
            self._server.cancel_active_stream()

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
        meeting_timestamp: Optional[str] = None,
        local_user_name: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Three-call schema-constrained grounded meeting intelligence extraction + verification:
        - Call 1A: Purpose, outcome, brief/detailed summaries, key points, structured topics
        - Call 1B: Atomic risks/blockers, truly unresolved open questions, explicit follow-ups
        - Call 2: Semantic decisions (confirmed/proposed/pending_confirmation) & atomic actions
        """
        if not self.is_available():
            return None
        self._server.ensure_started()
        ctx_now = int(getattr(self._server, "active_ctx_size", 16384) or 16384)
        is_cpu = self._server.active_device == "cpu"
        max_words = 3200 if ctx_now <= 8192 else (5800 if (is_cpu or ctx_now <= 16384) else 10500)
        call_max_tokens = 1400 if ctx_now <= 8192 else (1800 if is_cpu else 2300)
        transcript = _compact_transcript_lines(segments, max_words=max_words)
        if not transcript:
            return None

        dur_min, dur_rem = divmod(max(0, int(duration_sec)), 60)
        dur_str = f"{dur_min}m {dur_rem}s" if dur_min > 0 else f"{dur_rem}s"
        date_hint = f"Meeting Date: {str(meeting_timestamp)[:16]}\n" if meeting_timestamp else ""
        self_hint = f"Local mic speaker ('You (Mic)') is {local_user_name}.\n" if local_user_name else ""
        base_prefix = f"Meeting: {title}\n{date_hint}{self_hint}\nTRANSCRIPT:\n{transcript}"

        task1a_summary = (
            f"TASK:\nWrite an executive, user-perspective synthesis for this {platform} meeting (Duration: {dur_str}).\n"
            "STRICT QUALITY RULES:\n"
            "- Write as a senior chief of staff for someone who missed the meeting — NEVER copy raw transcript lines, speaker prefixes, greetings ('Hi Amar', 'Good morning', 'How are you'), or [MM:SS] timestamps into prose.\n"
            "- brief_summary: 3-4 crisp sentences summarizing the core purpose, key agreements, and primary outcome.\n"
            "- detailed_summary: 2-4 coherent editorial paragraphs covering context, key discussions, decisions made, risks raised, and next steps — 100% synthesized, never stitched conversation quotes.\n"
            "- key_discussion_points: 3-6 concise factual takeaways written clearly from the reader's perspective (no greetings, no timestamps, no 'Speaker said' framing).\n"
            "- topics: 2-5 distinct thematic topics with clean descriptive titles (2-6 words, NEVER sentence fragments like 'Hi Amar' or 'Today's meeting'), a 1-2 sentence synthesized summary, and clean bullets.\n"
            "- Keep internal timestamp_str in MM:SS format accurately for backend grounding only."
        )
        summary_data, tel1a = self._chat_json_with_retry(
            call_name="call1a_summary",
            messages=[
                {"role": "system", "content": _SHARED_MEETING_SYSTEM_PROMPT},
                {"role": "user", "content": f"{base_prefix}\n\n{task1a_summary}"},
            ],
            max_tokens=call_max_tokens,
            temperature=0.1,
            required_keys=("brief_summary", "detailed_summary", "topics", "key_discussion_points"),
            json_schema=CALL1A_SUMMARY_SCHEMA,
            schema_name="meeting_summary_topics",
        )

        task1b_risks_questions = (
            "TASK:\nExtract ONLY atomic risks/blockers, truly unresolved open questions, and pending follow-ups.\n"
            "Rules:\n"
            "- RISKS/BLOCKERS: Separate EACH risk or blocker into its own individual item cleanly. NEVER merge multiple risks into one paragraph and NEVER include labels like 'RISKImpact:'.\n"
            "- OPEN QUESTIONS: Include ONLY genuinely unanswered questions, pending external confirmations (e.g., finance/customer approval), or unfulfilled information requests. "
            "NEVER include greetings ('How are you'), context statements ('For context...'), clarification requests ('can you repeat'), or questions already answered yes/no in the meeting.\n"
            "- Keep risks <= 6, open_questions <= 6, follow_ups <= 7, with verbatim evidence_quote and MM:SS timestamp_str."
        )
        risk_q_data, tel1b = self._chat_json_with_retry(
            call_name="call1b_risks_questions",
            messages=[
                {"role": "system", "content": _SHARED_MEETING_SYSTEM_PROMPT},
                {"role": "user", "content": f"{base_prefix}\n\n{task1b_risks_questions}"},
            ],
            max_tokens=min(call_max_tokens, 1400),
            temperature=0.05,
            required_keys=("risks", "open_questions"),
            json_schema=CALL1B_RISKS_QUESTIONS_SCHEMA,
            schema_name="meeting_risks_open_questions",
        )

        owner_rule = (
            f"3. OWNER RULE: 'You (Mic)' is {local_user_name}. "
            if local_user_name
            else "3. OWNER RULE: "
        ) + (
            "Assign an explicit single owner ONLY when a participant clearly commits (e.g. 'I will / I'll / Let me...') or explicitly accepts a direct assignment. "
            "If an action uses collective 'we' without a clear single assignee or no owner is stated, set \"owner\": null. NEVER guess organizer or last speaker.\n"
        )
        task2_actions = (
            "TASK:\nExtract ONLY genuine decisions and genuine actionable commitments/next steps from the transcript above.\n"
            "CRITICAL RULES:\n"
            "1. DECISIONS vs PROPOSALS: Distinguish 'confirmed' (explicitly agreed) from 'proposed', 'under_discussion', 'pending_confirmation' (subject to validation/approval), or 'rejected'. Never turn individual tasks or general discussion into a confirmed decision.\n"
            "2. ATOMIC ACTIONS: Each action_items description MUST be a single concise verb-led task (max 22 words), NEVER merged tasks and NEVER greetings/farewells.\n"
            f"{owner_rule}"
            "4. DUE DATES: Put the exact spoken deadline phrase into \"due_date_text\" (e.g. 'today EOD', 'Monday', 'Tuesday', 'Wednesday', 'next week Thursday', '4th of December'); set null when not explicitly spoken.\n"
            "5. Keep rationale, context, and evidence_quote crisp (<= 15 words each). Extract up to 8 decisions and 12 action_items with MM:SS timestamp_str."
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
            json_schema=CALL2_DECISIONS_ACTIONS_SCHEMA,
            schema_name="meeting_decisions_actions",
        )

        self._last_reliability_telemetry = {
            "call1a_summary": tel1a,
            "call1b_risks_questions": tel1b,
            "call2_decisions_actions": tel2,
        }

        if not summary_data and not risk_q_data and not actions_data:
            return None

        merged: Dict[str, Any] = dict(summary_data or {})
        merged["call1a_ok"] = bool(summary_data)
        if not merged.get("executive_summary") and merged.get("brief_summary"):
            merged["executive_summary"] = merged["brief_summary"]
        if not merged.get("brief_summary") and merged.get("executive_summary"):
            merged["brief_summary"] = merged["executive_summary"]

        if risk_q_data:
            merged["risks"] = risk_q_data.get("risks", [])
            merged["open_questions"] = risk_q_data.get("open_questions", [])
            merged["follow_ups"] = risk_q_data.get("follow_ups", [])
            merged["call1b_ok"] = True
        else:
            merged["call1b_ok"] = False

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
        sanitized = self._sanitize_semantic_mom(merged)
        verified, ver_report = verify_and_normalize_intelligence(
            sanitized,
            segments,
            meeting_timestamp=meeting_timestamp,
            local_user_name=local_user_name,
        )
        verified["verification_report"] = ver_report
        return verified

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
            json_schema=CALL3_KNOWLEDGE_SCHEMA,
            schema_name="meeting_knowledge",
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
        conversation_history: Optional[List[Any]] = None,
        answer_depth: str = "detailed",
        structured_context: str = "",
        answer_intent: str = "general",
        local_user_name: str = "",
    ) -> List[Dict[str, str]]:
        excerpts: List[str] = []
        for i, c in enumerate(citations[:max(1, int(max_citations))], 1):
            m_title = c.get("meeting_title") or "Meeting"
            m_date = str(c.get("meeting_date") or "")[:10]
            date_tag = f" {m_date}" if m_date else ""
            ts = c.get("timestamp_formatted")
            if not ts:
                st = int(float(c.get("timestamp_sec") or c.get("start_time") or c.get("start_sec") or 0.0))
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

        struct_block = ""
        if structured_context and str(structured_context).strip():
            struct_block = f"\n\nAUTHORITATIVE STRUCTURED MEETING INTELLIGENCE:\n{str(structured_context).strip()[:3500]}"

        hist_block = ""
        if conversation_history:
            hist_lines: List[str] = []
            for entry in list(conversation_history)[-4:]:
                if isinstance(entry, dict):
                    hq_s = str(entry.get("question") or entry.get("q") or "").strip()
                    ha_s = str(entry.get("answer") or entry.get("a") or "").strip()[:360]
                elif isinstance(entry, (tuple, list)) and len(entry) >= 2:
                    hq_s = str(entry[0] or "").strip()
                    ha_s = str(entry[1] or "").strip()[:360]
                else:
                    continue
                if hq_s and ha_s:
                    hist_lines.append(f"User: {hq_s}\nAssistant: {ha_s}")
            if hist_lines:
                hist_block = "\n\nPRIOR CONVERSATION IN THIS SCOPE:\n" + "\n---\n".join(hist_lines)

        excerpts_text = "\n\n".join(excerpts)
        scope_info = f" (scoped ONLY to '{context_meeting_title}')" if context_meeting_title else " (across all scoped meetings)"
        user_ref = f" The local user ('You (Mic)') is {local_user_name}." if local_user_name else ""

        intent_norm = str(answer_intent or "general").strip().lower()
        format_guides = {
            "customer_notes": (
                "6. OUTPUT FORMAT (Customer-Ready Structured Meeting Notes):\n"
                "   - Start with `### Meeting Notes — <Title>` plus **Date** and **Focus**.\n"
                "   - Add `### Key Discussion Points` grouped by crisp topic subheadings (`#### Topic`) with concise bullets.\n"
                "   - Add `### Actions & Target Dates` as a clean Markdown table `| Action | Owner | Target Date |` using 'Owner needed' whenever no single owner was stated.\n"
                "   - Add `### Open Confirmations & Risks` (strictly grounded; zero filler or raw citation tags)."
            ),
            "follow_up_email": (
                "6. OUTPUT FORMAT (Professional Follow-Up Email):\n"
                "   - Output **Subject:** line, clean greeting, brief context recap, agreed action items with ONLY verified owners and dates (`Owner needed` / `Date TBD` if omitted), and **Open Confirmations**.\n"
                "   - Never invent recipients, promises, commercial terms, or unstated deadlines."
            ),
            "speaker_qa": (
                "6. OUTPUT FORMAT (Speaker-Attributed Answer):\n"
                "   - Use clear natural Markdown with exact speaker attribution.\n"
                "   - Note clearly if a speaker is labeled generically (e.g. `Speaker 1`) rather than confirmed by name."
            ),
            "multi_meeting_recap": (
                "6. OUTPUT FORMAT (Multi-Meeting Notes & Personal Action Plan):\n"
                "   - Group concise notes by meeting (`### Meeting Title — Date`).\n"
                "   - End with a clean Markdown action table `| Meeting | Action | Owner | Target Date |` highlighting tasks for the requested owner/self."
            ),
            "action_items": (
                "6. OUTPUT FORMAT (Action Plan Table):\n"
                "   - Present a clean Markdown table `| Action | Owner | Target Date | Status |`. Use 'Owner needed' for unassigned tasks."
            ),
            "decisions": (
                "6. OUTPUT FORMAT (Decisions Breakdown):\n"
                "   - Clearly separate **Confirmed Decisions**, **Proposed / Pending Confirmation**, and **Rejected / Unresolved**."
            ),
            "risks_open_items": (
                "6. OUTPUT FORMAT (Risks, Blockers & Unresolved Items):\n"
                "   - List each risk/blocker separately (**Description**, **Impact**, **Mitigation**) and then **Unresolved Questions & Pending Confirmations**."
            ),
        }
        format_rule = format_guides.get(
            intent_norm,
            (
                "6. Keep the answer crisp and concise (3-5 bullet points max) with natural conversational Markdown."
                if str(answer_depth or "detailed").lower() == "concise"
                else "6. Choose clean structured Markdown (short heading or direct answer, concise bullets/tables when helpful, and clearly state when requested details were not discussed)."
            ),
        )

        sys_msg = (
            "You are VOX, a strictly grounded offline meeting intelligence assistant.\n"
            f"Rules:{user_ref}\n"
            "1. Answer ONLY from the provided Authoritative Structured Intelligence, Meeting Excerpts, and Verified Knowledge Memory.\n"
            "2. Present natural, readable answers using exact speaker names and dates when relevant, without appending bracketed timestamp tags (`[MM:SS]`), reference numbers (`[1]`), or a 'Sources:' footer.\n"
            "3. CRITICAL SPEAKER ATTRIBUTION: Each transcript line is tagged `[MM:SS|ExactSpeaker]`. Attribute every statement strictly to the ExactSpeaker tag on that line. NEVER merge two different speaker names into one person or present an unverified generic speaker as named without noting uncertainty.\n"
            "4. CRITICAL REFUSAL RULE: If the answer or requested detail (number, budget, date, decision, owner, topic) is NOT present in the provided excerpts, state naturally and honestly that it was not discussed in the meeting without guessing or extrapolating.\n"
            "5. If excerpts span multiple meetings, distinguish each meeting clearly by title and date.\n"
            f"{format_rule}"
        )
        usr_msg = (
            f"MEETING EXCERPTS:\n{excerpts_text}{struct_block}{k_block}{hist_block}\n\n"
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
        conversation_history: Optional[List[Any]] = None,
        answer_depth: str = "detailed",
        structured_context: str = "",
        answer_intent: str = "general",
        local_user_name: str = "",
    ) -> Optional[str]:
        if not citations and not knowledge_items and not structured_context:
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
            structured_context=structured_context,
            answer_intent=answer_intent,
            local_user_name=local_user_name,
        )
        return self._server.chat(msgs, max_tokens=1100, temperature=0.1)

    def answer_query_stream(
        self,
        question: str,
        citations: List[Dict[str, Any]],
        context_meeting_title: Optional[str] = None,
        knowledge_items: Optional[List[Dict[str, Any]]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        conversation_history: Optional[List[Any]] = None,
        answer_depth: str = "detailed",
        structured_context: str = "",
        answer_intent: str = "general",
        local_user_name: str = "",
    ) -> Iterator[str]:
        if not citations and not knowledge_items and not structured_context:
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
            structured_context=structured_context,
            answer_intent=answer_intent,
            local_user_name=local_user_name,
        )
        for chunk in self._server.chat_stream(
            msgs, max_tokens=1100, temperature=0.1, cancel_check=cancel_check
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
            from voxlocal.engine.intelligence_verifier import clean_user_facing_text, is_greeting_or_chatter
            cleaned_kp = []
            for k in data["key_discussion_points"]:
                if isinstance(k, dict):
                    top = clean_user_facing_text(str(k.get("topic") or ""))
                    sm = clean_user_facing_text(str(k.get("summary") or ""))
                    if (top or sm) and not is_greeting_or_chatter(sm or top):
                        nk = dict(k)
                        nk["topic"] = top
                        nk["summary"] = sm
                        cleaned_kp.append(nk)
            data["key_discussion_points"] = cleaned_kp

        if "topics" in data and isinstance(data["topics"], list):
            from voxlocal.engine.intelligence_verifier import clean_user_facing_text, is_greeting_or_chatter
            cleaned_topics = []
            for t in data["topics"]:
                if isinstance(t, dict):
                    ttl = clean_user_facing_text(str(t.get("title") or ""))
                    sm = clean_user_facing_text(str(t.get("summary") or ""))
                    if (ttl or sm) and not (is_greeting_or_chatter(ttl) and is_greeting_or_chatter(sm)):
                        nt = dict(t)
                        nt["title"] = ttl or "Discussion Topic"
                        nt["summary"] = sm
                        cleaned_topics.append(nt)
            data["topics"] = cleaned_topics

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
                if isinstance(r, dict) and str(r.get("risk") or r.get("description") or "").strip()
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

# Dispatch Task: Track 2 — Intelligence & RAG Lead

- **Assigned Subagent**: `worker_2_intelligence`
- **Archetype**: `teamwork_worker`
- **Role**: Intelligence & RAG Lead
- **Working Directory**: `d:\Milind\Voice Assistant\.agents\teamwork\worker_2_intelligence\`
- **Exclusive File Ownership**:
  - `src/voxlocal/engine/meeting_intelligence.py`
  - `src/voxlocal/engine/llm_engine.py`
  *(You MUST NOT edit any other files)*

## Mandatory Integrity Warning
> DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Reference Specifications & Blueprints
- `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`
- `d:\Milind\Voice Assistant\.agents\teamwork\orchestrator\SCHEMA_AND_MIGRATION_SPECIFICATION.md`
- `d:\Milind\Voice Assistant\.agents\teamwork\orchestrator\FILE_LEVEL_IMPLEMENTATION_PLAN.md`
- `d:\Milind\Voice Assistant\.agents\teamwork\orchestrator\DEFECT_REGISTER_AND_ADVERSARIAL_CATALOGUE.md`

## Required Implementations
1. **Canonical Chronological Normalization in `compare_meetings`**:
   - Ensure meeting A is earlier in time and meeting B is later in time regardless of the UI argument order (based on `created_at` or ISO timestamp).
   - If timestamps are equal or unparseable, preserve stable deterministic ordering.
2. **Decoupled 7-State Decision Evolution Matcher**:
   - Standardize strictly on canonical field name `evolution_relationship` (never `evolution_state`).
   - Valid values: `'new'`, `'repeated'`, `'superseded'`, `'reversed'`.
   - Maintain distinct business `status`: `'proposed'`, `'confirmed'`, `'rejected'`, `'unresolved'`, `'unclassified'`.
   - When a later meeting updates a decision, set `supersedes_id` referencing the earlier decision ID. Never overwrite or delete earlier decisions or their supporting evidence.
3. **Structured Relative Date Resolution with Explicit Uncertainty**:
   - In `extract_action_items`, capture relative spoken date expressions (e.g. "next Friday", "by tomorrow", "in 2 weeks").
   - Resolve to calendar date when reference timestamp and timezone are known.
   - Serialize structured dictionary to JSON in `due_date_resolution`:
     `{"original_text": "...", "resolved_date": "YYYY-MM-DD" or null, "reference_timestamp": "...", "timezone": "...", "confidence": 0.0-1.0, "uncertainty_reason": "..."}`.
   - Do NOT invent calendar dates when reference is ambiguous or timezone is missing; set `resolved_date: null` and explain uncertainty.
4. **Pre-Meeting Briefings with Anchor Citations**:
   - In pre-meeting briefings, include explicit temporal anchor citations (`[YYYY-MM-DD HH:MM:SS]` or timestamp + meeting title) for prior commitments and decisions.
5. **Ask Vox RAG Isolation & Hygiene**:
   - In `ask_vox`, query meetings by exact meeting ID or active scope without 250-character truncation of context chunks.
   - When 0 matches are found, return clear "No relevant meeting context found" rather than leaking unrelated cross-meeting data.
   - Implement post-retrieval quarantine ensuring retrieved chunks match the queried session context.

## Verification
- Test all changes using isolated temporary databases/mocks.
- Ensure no broken imports or syntax errors.
- Write handoff report in `d:\Milind\Voice Assistant\.agents\teamwork\worker_2_intelligence\handoff.md`.
- Send completion message to orchestrator.

## 2026-11-02T03:28:18Z
[Message] timestamp=2026-11-02T03:28:18Z sender=1cc3eed8-379d-49d6-9ea3-f6ce582a2d0b priority=MESSAGE_PRIORITY_HIGH content=You are teamwork_worker assigned to Track 2: Intelligence & RAG Lead.
Your working directory is: d:\Milind\Voice Assistant\.agents\teamwork\worker_2_intelligence\
Your exclusive file ownership:
- `src/voxlocal/engine/meeting_intelligence.py`
- `src/voxlocal/engine/llm_engine.py`
You MUST NOT edit any other files.

## 2026-11-02T03:46:57Z
[Message] timestamp=2026-11-02T03:46:57Z sender=5e6806a0-e398-45c0-86c7-671db54c88e0 priority=MESSAGE_PRIORITY_HIGH content=**Context**: Track 6 regression verification on MeetingMinutesPipeline
**Content**: In `src/voxlocal/engine/meeting_intelligence.py:1852`, `cls._stage5_extract_action_plan` is called with keyword arguments `meeting_timestamp=meeting_timestamp, timezone=timezone`, but its definition at line 2200 is `def _stage5_extract_action_plan(cls, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:`. This causes `TypeError: unexpected keyword argument 'meeting_timestamp'` in `MeetingMinutesPipeline.generate_minutes`.
**Action**: Please update `_stage5_extract_action_plan` definition to accept `meeting_timestamp: Optional[str] = None, timezone: Optional[str] = None, **kwargs`.

## 2026-11-02T03:52:13Z
[Message] timestamp=2026-11-02T03:52:13Z sender=5e6806a0-e398-45c0-86c7-671db54c88e0 priority=MESSAGE_PRIORITY_HIGH content=**Context**: Track 6 QA regression test analysis for Track 2 (Meeting Intelligence)
**Content**: Two minor regressions were detected in `src/voxlocal/engine/meeting_intelligence.py` during the 12-suite regression run:
1. Line 1252 (Ask Vox Zero Match): `test_meeting_intelligence.py:136` asserts `self.assertIn("No Direct Matches", res["answer"])`. Updating the markdown header to `f"### 🔍 No Direct Matches / No Relevant Meeting Context Found\n\n"` will satisfy the test and keep the clean explanation.
2. Line 2280 (`_stage5_extract_action_plan` fallback): When `meeting_timestamp` is not provided (or relative date cannot resolve to calendar date), `res_obj.get("resolved_date")` is None. Setting `due_date = res_obj["resolved_date"] if res_obj.get("resolved_date") else (raw_due.strip().capitalize() if raw_due else "Not specified")` preserves the human-readable text (e.g. "By eod today") in `due_date` as expected by `tests/test_core_feature3_meeting_studio_audit.py:399`, while `due_date_resolution` correctly preserves the structured JSON metadata.
**Action**: Please incorporate these two small adjustments into `src/voxlocal/engine/meeting_intelligence.py` so the full 98-test regression suite passes 100%.

## 2026-11-02T03:58:36Z
[Message] timestamp=2026-11-02T03:58:36Z sender=5e6806a0-e398-45c0-86c7-671db54c88e0 priority=MESSAGE_PRIORITY_HIGH content=**Context**: Track 6 QA regression test analysis for Track 2 (Meeting Intelligence)
**Content**: Confirmed and verified! With your updates incorporated, the complete 12-suite regression run (`scratch/run_suites_clean.py`) completed with 98/98 PASS (0 failures, 0 errors in 30.6s). Combined with the 44 Phase 6 tests (MIG-01..10 and ADV-01..34), the entire test suite achieves 142/142 (100%) clean passes.
**Action**: None needed; thank you for the quick and precise updates!

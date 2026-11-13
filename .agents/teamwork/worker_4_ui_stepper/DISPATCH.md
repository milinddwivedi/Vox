# Dispatch Task: Track 4 — UI & Presentation Lead

- **Assigned Subagent**: `worker_4_ui_stepper`
- **Archetype**: `teamwork_worker`
- **Role**: UI & Presentation Lead
- **Working Directory**: `d:\Milind\Voice Assistant\.agents\teamwork\worker_4_ui_stepper\`
- **Exclusive File Ownership**:
  - `src/voxlocal/ui/main_window.py`
  - `src/voxlocal/ui/components.py`
  *(You MUST NOT edit any other files)*

## Mandatory Integrity Warning
> DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Reference Specifications & Blueprints
- `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`
- `d:\Milind\Voice Assistant\.agents\teamwork\orchestrator\SCHEMA_AND_MIGRATION_SPECIFICATION.md`
- `d:\Milind\Voice Assistant\.agents\teamwork\orchestrator\FILE_LEVEL_IMPLEMENTATION_PLAN.md`
- `d:\Milind\Voice Assistant\.agents\teamwork\orchestrator\DEFECT_REGISTER_AND_ADVERSARIAL_CATALOGUE.md`

## Required Implementations in `src/voxlocal/ui/main_window.py` & `src/voxlocal/ui/components.py`
1. **Direct `meeting_id` Scoping in In-Report Search**:
   - In report search bar, pass the explicit `meeting_id` of the currently opened meeting directly to the database search/filter methods rather than searching globally across all meetings without ID scoping.
2. **Truthful Presentation Templates (Eliminate Fact-Fabrication Fallbacks)**:
   - When presentation templates are selected, do not inject fabricated dummy bullet points or speculative claims if intelligence extraction was partial or empty. Render honest placeholders indicating "No items extracted for this section".
3. **Canonical Database KPI Widget Updates**:
   - Bind the dashboard Action Items KPI count directly to the canonical SQL open definition: `status IN ('pending', 'blocked', 'reopened')`.
   - Remove any client-side hardcoded 10-item display caps so the true count of open work is displayed accurately without artificial truncation.
4. **Persistent Live Markers UI**:
   - Ensure "⚡ Flag Action" and "🔖 Bookmark" buttons during live recordings save persistent markers into the `meeting_markers` table via database methods with relative timestamp and note.
5. **Slack Export & Export Preview Safeguards**:
   - In Slack / Markdown export actions, wire to `TranscriptExporter.check_sensitive_content(meeting)`.
   - If sensitive keywords are detected, display an explicit confirmation dialog warning the user before copying to the clipboard or writing to external files.
   - For clipboard copies, do NOT insert unconditional `QApplication.processEvents()` inside production handlers (let standard Qt event handling manage clipboard buffers).
   - Use canonical active decisions in Slack exports.

## Verification
- Run tests in isolated test environments.
- Verify UI component instantiation and signal bindings.
- Write handoff report in `d:\Milind\Voice Assistant\.agents\teamwork\worker_4_ui_stepper\handoff.md`.
- Send completion message to orchestrator.


## 2026-11-02T03:28:33Z
You are teamwork_worker assigned to Track 4: UI & Presentation Lead.
Your working directory is: d:\Milind\Voice Assistant\.agents\teamwork\worker_4_ui_stepper\
Your exclusive file ownership:
- `src/voxlocal/ui/main_window.py`
- `src/voxlocal/ui/components.py`
You MUST NOT edit any other files.

Reference documents:
- d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md
- d:\Milind\Voice Assistant\.agents\teamwork\orchestrator\SCHEMA_AND_MIGRATION_SPECIFICATION.md
- d:\Milind\Voice Assistant\.agents\teamwork\orchestrator\FILE_LEVEL_IMPLEMENTATION_PLAN.md
- d:\Milind\Voice Assistant\.agents\teamwork\orchestrator\DEFECT_REGISTER_AND_ADVERSARIAL_CATALOGUE.md
- d:\Milind\Voice Assistant\.agents\teamwork\worker_4_ui_stepper\DISPATCH.md

MANDATORY INTEGRITY WARNING:
> DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

TASKS:
1. In `src/voxlocal/ui/main_window.py` & `src/voxlocal/ui/components.py`:
   - Direct `meeting_id` scoping in in-report search: Pass `meeting_id` of the active meeting directly to database search/filter methods rather than performing an unscoped global search.
   - Truthful presentation templates: Remove fact-fabrication fallbacks. When template sections are empty or unpopulated, display an honest placeholder ("No items extracted for this section") rather than inventing dummy text.
   - Canonical database KPI widget updates: Bind the dashboard Action Items KPI count directly to canonical SQL query: `status IN ('pending', 'blocked', 'reopened')`. Remove any hardcoded client-side display limits (like 10 items) so the true open count is accurately displayed.
   - Persistent live markers UI: Ensure "⚡ Flag Action" and "🔖 Bookmark" buttons during live recordings save markers to SQLite `meeting_markers` via `db.add_meeting_marker(meeting_id, marker_type, elapsed_sec, note)`.
   - Slack & Export preview safeguards: Wire export actions to `TranscriptExporter.check_sensitive_content(meeting)`. If sensitive keywords are detected, display an explicit confirmation dialog before copying or saving. For clipboard copies, do NOT insert unconditional `QApplication.processEvents()` inside production handlers. Use canonical active decisions in Slack exports.
2. Test your implementation against isolated test environments. Verify no regressions on existing UI tests.
3. Write your completion report in `d:\Milind\Voice Assistant\.agents\teamwork\worker_4_ui_stepper\handoff.md` and update `progress.md`.
4. Send completion message to orchestrator.


## 2026-11-02T03:48:18Z
From: Track 6 QA Lead (5e6806a0-e398-45c0-86c7-671db54c88e0)
Context: Track 6 regression verification on test_meeting_studio_phase5_enterprise_ux (test_06_in_report_ask_vox_scoped_qa)
Content: `test_06_in_report_ask_vox_scoped_qa` expects `call_arg = mock_query.call_args[0][0]` to contain the meeting title (e.g. "Everlane"). To preserve 100% backward compatibility with this baseline test while fulfilling ADV-31 (direct meeting_id scoping), in `_submit_in_report_ask_query`, format the query argument as `f"In [{m.get('title', '')}] meeting: {q}"` while also passing `meeting_id=self._selected_meeting_id`.
Action: Please update `_submit_in_report_ask_query` in `main_window.py` to format the question with the title prefix AND pass `meeting_id=self._selected_meeting_id`.

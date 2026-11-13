# Specification & Architecture Mining Report: Milestone 1
**Domain**: Cross-Meeting Intelligence, Privacy, Dashboard KPI Integrity & Query Scoping (Focus Areas 4, 5, 7, 8)
**Working Directory**: `d:\Milind\Voice Assistant\.agents\teamwork\spec_miner_m1_cross\`
**Author**: `teamwork_spec_miner`
**Reference Document**: `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`
**Mode**: STRICTLY READ-ONLY ARCHITECTURAL DISCOVERY & SPECIFICATION

---

## 1. Executive Summary & Scope

This report provides the authoritative specification, data-ownership map, defect taxonomy, and architectural remediation blueprint for Milestone 1 across four core enterprise capability areas:
1. **Focus Area 4: Dashboard KPI Integrity** (`Meetings Recorded`, `Total Talk Time`, `Open Action Items`, `Strategic Decisions`).
2. **Focus Area 5: Report Templates & Export Privacy** (Fact preservation, Client Review redaction, internal-notes leakage prevention, export confirmation safeguards).
3. **Focus Area 7: Cross-Meeting Intelligence & Pre-Meeting Briefings** (Evidence-grounded briefings, blocker/risk lifecycle tracking, prevention of task revival without new evidence).
4. **Focus Area 8: Meeting-Scoped Ask Vox Queries** (Strict isolation, zero cross-meeting leakage in citations/answers/suggestions/fallbacks, RAG and keyword search filtering).

---

## 2. Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | Focus Area 4 | Dashboard KPI Calculation | Aggregates high-level metrics for `Recorded`, `Talk Time`, `Open Actions`, `Decisions` on the Meeting Studio executive header | `LocalDatabase` records | Updated QLabel widgets (`val_m`, `val_h`, `val_a`, `val_d`) | Silently logs debug exception; truncates counts if records exceed hardcoded limits (1,000 / 5,000) | `src/voxlocal/ui/main_window.py:1961` & `src/voxlocal/ui/components.py:1610` |
| 2 | Focus Area 4 | Database Analytics Aggregator | Standalone SQLite query method aggregating meeting counts, durations, and action item statuses | None | Dictionary with `total_meetings`, `total_duration_sec`, `pending_action_items`, etc. | Coalesces null durations to 0.0; omits decision counts entirely | `src/voxlocal/storage/database.py:998` |
| 3 | Focus Area 4 | Action Item Status Toggle | Flips status between `pending` and `completed` for a given action item record | `item_id: int`, optional `new_status: str` | Updated status string | Returns `"pending"` if record is missing | `src/voxlocal/storage/database.py:730` |
| 4 | Focus Area 4 | Meeting Outcomes Clearing | Deletes auto-generated action items and decisions for a meeting prior to re-analysis | `meeting_id: int`, `preserve_completed: bool = True` | None (SQL transaction) | Deletes all decisions; deletes non-completed actions if `preserve_completed=True` | `src/voxlocal/storage/database.py:651` |
| 5 | Focus Area 5 | Report Presentation Templates | Formats meeting summary into specialized styles: Standard MoM, Executive Brief, Internal Status, Client Review, Technical Troubleshooting | `meeting: dict`, `template_name: str`, `action_records: list`, `decision_records: list` | Formatted plain text string displayed in `txt_summary` | If raw data is empty, generates hardcoded fabricated statements (e.g. "milestone sign-off completed") | `src/voxlocal/ui/main_window.py:5114` |
| 6 | Focus Area 5 | Direct File Exporter | Writes meeting transcript and metadata to disk in TXT, Markdown, SRT, or VTT formats | `meeting: Dict[str, Any]`, `target_path: Path`, `fmt: str` | Path to written file | Raises `ValueError` on unsupported format; completely ignores active presentation template | `src/voxlocal/storage/exporter.py:41` & `src/voxlocal/ui/main_window.py:5338` |
| 7 | Focus Area 5 | User-Edited Report Protection | Sets `user_edited=1` in `meetings` table when manual edits occur to prevent silent overwrite | `meeting_id: int`, `is_edited: bool = True` | None | Updates database column | `src/voxlocal/storage/database.py:296` |
| 8 | Focus Area 7 | Executive Pre-Meeting Briefing | Generates instant executive briefing markdown synthesizing past relevant sessions, decisions, open actions, and agenda | `query_or_topic: str` | Dict with `topic`, `relevant_meetings`, `open_action_items`, `key_decisions`, `briefing_markdown` | Returns generic fallback agenda if no matches; lacks date and timestamp citations on items | `src/voxlocal/engine/meeting_intelligence.py:530` |
| 9 | Focus Area 7 | Pre-Meeting Briefing Modal | Interactive dialog with topic selector, preset chips (Customer, Product, Sprint), Markdown preview, and Slack/Teams copy | `db`, `intelligence`, `theme`, `initial_topic` | QDialog UI | Gracefully handles empty topics | `src/voxlocal/ui/components.py:1820` |
| 10 | Focus Area 7 | Algorithmic Meeting Comparison | Compares two meeting sessions side-by-side (metadata, decision evolution, action carry-over, topic shifts) | `meeting_id_a: int`, `meeting_id_b: int` | Dict with comparison markdown, carry-over actions, topic diffs | Raises `ValueError` if either meeting does not exist; enforces chronological ordering | `src/voxlocal/engine/meeting_intelligence.py:415` |
| 11 | Focus Area 7 | Recurring Topic Aggregation | Extracts catalog-wide discussion topics and frequency across recorded sessions | `limit: int = 15` | List of dicts with topic name, count, and meeting references | Filters out stopwords and short tokens (< 3 chars) | `src/voxlocal/engine/meeting_intelligence.py:636` |
| 12 | Focus Area 8 | Natural Language Memory Search | Keyword and token-based multi-table search across transcript segments, decisions, actions, and summaries | `query: str`, `limit: int = 15`, `meeting_id: Optional[int] = None` | List of deduplicated citation dicts with timestamp and speaker | Returns empty list if query is whitespace-only; filters stopwords | `src/voxlocal/storage/database.py:826` |
| 13 | Focus Area 8 | Cognitive Query Intent Classifier | Categorizes user queries into SUMMARY, ACTION_ITEMS, DECISIONS, TOPIC_QA, SPEAKER_QA, TIMELINE, GENERAL | `query: str` | Tuple of `(intent, content_tokens, scoped_title)` | Falls back to TOPIC_QA if no cues match; extracts scoped meeting title via regex | `src/voxlocal/engine/meeting_intelligence.py:660` |
| 14 | Focus Area 8 | Grounded Ask Vox Query Engine | Coordinates multi-stage retrieval, scoping, LLM synthesis, or algorithmic template synthesis | `question: str`, `meeting_id: Optional[int] = None` | Dict with `question`, `answer`, `citations`, `intent`, `suggestions` | Catches exceptions and returns error message markdown with suggestions | `src/voxlocal/engine/meeting_intelligence.py:110` |
| 15 | Focus Area 8 | Real-Time Ask Vox Streamer | Streams answer tokens in real time via local LLM or 12ms-paced cognitive synthesizer; yields early citations | `question: str`, `meeting_id: Optional[int]`, callbacks | Dict with full answer and citations; emits Qt signals | Checks `cancel_check` callback to terminate on user cancellation | `src/voxlocal/engine/meeting_intelligence.py:230` & `src/voxlocal/core/controller.py:2608` |
| 16 | Focus Area 8 | Meeting Scope Selector UI | Dropdown in Ask Vox tab allowing user to scope questions to a specific meeting or entire library | UI event `currentIndexChanged` | Scoped meeting ID passed to executor | Capped at 50 meetings; does not update when in-report queries execute | `src/voxlocal/ui/main_window.py:1981, 2047` |
| 17 | Focus Area 8 | In-Report Ask Vox Capsule | Embedded search bar within the Meeting Report view intended to query the currently viewed meeting | User input in `txt_in_report_ask` | Renders Markdown answer in `txt_in_report_ans` | Fails to pass `meeting_id`; prefixes string with title and risks global leak | `src/voxlocal/ui/main_window.py:1904` |

---

## 3. Edge Cases & Observed Behaviors

| # | Feature | Input / Condition | Observed Behavior | Defect / Risk Classification |
|---|---------|-------------------|-------------------|-----------------------------|
| 1 | Dashboard KPI Integrity | Database contains > 1,000 meetings or > 5,000 action items | `_refresh_meeting_dashboard_stats` queries `list_meetings(limit=1000)` and `list_action_items(limit=5000)`. Metrics truncate at 1,000 sessions and 5,000 tasks. | **Data-Integrity Defect / Metric Truncation** |
| 2 | Dashboard KPI Integrity | Meeting session crashed during recording (`status='recording'`) or failed transcribing (`status='transcribing'`) | Meeting record is counted in `total_m = len(meetings)` and its partial `duration_sec` is added to Total Talk Time. | **Data-Integrity Defect / Dirty State Inclusion** |
| 3 | Dashboard KPI Integrity | Action item status is `'cancelled'`, `'blocked'`, or `'deferred'` | `pending_actions = sum(1 for a in all_actions if a.get("status") != "completed")` counts cancelled and blocked items as "Open Actions". Conficts with `LocalDatabase.get_analytics_summary()` which queries `WHERE status = 'pending'`. | **Logical Inconsistency / Misleading KPI** |
| 4 | Dashboard KPI Integrity | Meeting is re-analyzed with local AI after completing some tasks | `clear_meeting_outcomes` preserves completed items. If LLM generates slightly rephrased version of the same task, `add_action_item` string matching fails, creating duplicate pending task. Total actions and pending actions inflate. | **Data-Integrity Defect / Duplication on Regeneration** |
| 5 | Dashboard KPI Integrity | Strategic decision is superseded, rejected, or reversed in a follow-up meeting | Database `decisions` table has no status or lifecycle column. Every decision ever inserted is permanently counted in `total_decisions = len(all_decisions)`. | **Data-Integrity Risk / Dead Commitment Accumulation** |
| 6 | Report Templates | Meeting has no summary or decisions recorded, user selects "Client Review" template | Template generator asserts: `"Client review and milestone sign-off completed."` and `"• Terms accepted as discussed."` | **Fact-Fabrication Defect (Violates Fact Preservation)** |
| 7 | Report Templates | Meeting has no summary or decisions, user selects "Executive Brief" or "Internal Status" | Template generator asserts: `"Discussion concluded successfully without major blockers."` and `"• Decisions logged in sprint board."` | **Fact-Fabrication Defect (Violates Fact Preservation)** |
| 8 | Report Export Privacy | User selects "Client Review" template in UI, then clicks "Export Markdown" or "Export TXT" | `_export_selected()` calls `TranscriptExporter.export_to_file(m, fmt)`, completely bypassing the template and dumping raw transcript, internal notes, and unredacted dialog. | **Privacy / Feature Disconnection Defect** |
| 9 | Report Export Privacy | Meeting contains confidential internal notes (e.g. margins, internal disputes, salary discussions) in `notes` or transcript | Exporting via `TranscriptExporter` writes `meeting["notes"]` under `## Notes` and full dialog verbatim without any redacting, sensitivity warning, or confirmation modal. | **Client Review Privacy Leak Defect** |
| 10 | Pre-Meeting Briefing | User requests briefing on topic with past sessions | Briefing lists decisions without meeting dates or timestamps (`timestamp_sec` omitted). Action items omit timestamp anchors and session IDs. | **Missing Attribution / Citation Defect** |
| 11 | Pre-Meeting Briefing | A task was completed or a blocker was resolved in a subsequent meeting, but earlier meeting has `status='pending'` | Briefing generator blindly selects `list_action_items(status='pending')` from earlier meeting. Revives resolved/completed item as active open carry-over work without cross-meeting evidence reconciliation. | **Logical Defect / Stale Task Revival** |
| 12 | Pre-Meeting Briefing | Risks and blockers identified in past meetings | `LocalDatabase` has no `risks` or `blockers` table. Risks generated by `MeetingMinutesPipeline` are trapped in `summary` text; briefing engine cannot reliably track active vs resolved blockers. | **Missing Capability / Schema Deficiency** |
| 13 | Meeting-Scoped Ask Vox | User queries specific meeting via in-report search box (`_submit_in_report_ask_query`) | Controller is invoked with `f"In {m['title']} meeting: {q}"` without `meeting_id`. Regex fails on special characters or titles without clean word boundaries; falls back to catalog-wide search, leaking citations from unrelated meetings. | **Severe Scoping Defect / Cross-Meeting Contamination** |
| 14 | Meeting-Scoped Ask Vox | Scoped query has no matching text in the targeted meeting | `_synthesize_answer()` prints: `"I searched across your recorded meetings... Available recorded meetings: <titles of 4 unrelated meetings>"`. Suggestions also leak unrelated meeting titles. | **Information Leakage Defect** |
| 15 | Meeting-Scoped Ask Vox | Targeted meeting is older than the 250 most recent meetings (`id > 250` from top) | `all_meetings = self.db.list_meetings(limit=250)` fails to contain the scoped meeting. `scoped_meeting` becomes `None`. Engine falls back to pulling action items and decisions from `all_meetings[:3]`! Returns actions/decisions from 3 unrelated meetings. | **Severe Scoping Defect / Silent Catalog Fallback** |
| 16 | Meeting-Scoped Ask Vox | Ask Vox UI scope dropdown (`combo_ask_meeting_scope`) has > 50 meetings | Dropdown query uses `limit=50`. Older meetings cannot be selected from the UI scope dropdown. | **UX Ambiguity / Truncation Defect** |

---

## 4. Five-Component Handoff Report

### 4.1. Observation

#### Observation 1: In-Memory Dashboard Calculation and Status Inconsistencies
In `src/voxlocal/ui/main_window.py` (lines 1961–1980):
```python
1961:     def _refresh_meeting_dashboard_stats(self) -> None:
1962:         try:
1963:             if not hasattr(self, "meeting_dashboard_header") or not self.meeting_dashboard_header:
1964:                 return
1965:             meetings = self.controller.db.list_meetings(limit=1000)
1966:             total_m = len(meetings)
1967:             total_sec = sum(float(m.get("duration_sec") or 0.0) for m in meetings)
1968:             all_actions = self.controller.db.list_action_items(limit=5000)
1969:             pending_actions = sum(1 for a in all_actions if a.get("status") != "completed")
1970:             all_decisions = self.controller.db.list_decisions(limit=5000)
1971:             total_decisions = len(all_decisions)
1972:             self.meeting_dashboard_header.update_stats(
1973:                 total_meetings=total_m,
1974:                 total_sec=total_sec,
1975:                 pending_actions=pending_actions,
1976:                 total_decisions=total_decisions,
1977:             )
```
In contrast, in `src/voxlocal/storage/database.py` (lines 1001–1006):
```python
1001:             tot_meetings = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
1002:             tot_duration = conn.execute("SELECT SUM(duration_sec) FROM meetings").fetchone()[0] or 0.0
1003:             avg_engagement = conn.execute("SELECT AVG(engagement_score) FROM meetings").fetchone()[0] or 85.0
1004:             tot_actions = conn.execute("SELECT COUNT(*) FROM action_items").fetchone()[0]
1005:             pending_actions = conn.execute("SELECT COUNT(*) FROM action_items WHERE status = 'pending'").fetchone()[0]
1006:             completed_actions = conn.execute("SELECT COUNT(*) FROM action_items WHERE status = 'completed'").fetchone()[0]
```
Neither implementation filters for `status = 'completed'` on meetings. Incomplete, failed, or active recordings are included. In `main_window.py`, `a.get("status") != "completed"` counts cancelled and blocked items as pending, whereas `database.py` requires `status = 'pending'`.

#### Observation 2: Fact-Fabrication Fallbacks in Presentation Templates
In `src/voxlocal/ui/main_window.py` (lines 5134–5238):
- Line 5135: `raw_summary if raw_summary else "Discussion concluded successfully without major blockers."`
- Line 5164: `raw_summary if raw_summary else "Progress reviewed according to sprint schedule."`
- Line 5182: `lines.append("• Decisions logged in sprint board.")`
- Line 5192: `raw_summary if raw_summary else "Client review and milestone sign-off completed."`
- Line 5202: `lines.append("• Deliverables documented in statement of work.")`
- Line 5209: `lines.append("• Terms accepted as discussed.")`
- Line 5229: `lines.append("• Root cause investigation underway.")`
- Line 5238: `lines.append("• Diagnostics tasks in progress.")`

#### Observation 3: Disconnection Between Selected Template and File Export
In `src/voxlocal/ui/main_window.py` (lines 5338–5352):
```python
5338:     def _export_selected(self, fmt: str) -> None:
5339:         if self._selected_meeting_id is None:
5340:             return
5341:         m = self.controller.db.get_meeting(self._selected_meeting_id)
5342:         if not m:
5343:             return
...
5350:         TranscriptExporter.export_to_file(m, Path(path), fmt=fmt)
5351:         self.show_toast("Exported", f"Successfully exported to {path}")
```
In `src/voxlocal/storage/exporter.py` (lines 45–115):
`TranscriptExporter.to_txt` and `TranscriptExporter.to_markdown` read directly from `meeting.get("summary")`, `meeting.get("notes")`, and `meeting.get("segments")`. The presentation template configured in `self.combo_report_template` is completely ignored during file export. `meeting["notes"]` and all transcript segments are exported without audience sensitivity checks.

#### Observation 4: Meeting-Scoped In-Report Ask Vox Drops Meeting ID
In `src/voxlocal/ui/main_window.py` (lines 1904–1918):
```python
1904:     def _submit_in_report_ask_query(self) -> None:
1905:         q = self.txt_in_report_ask.text().strip()
1906:         if not q:
1907:             return
1908:         if self._selected_meeting_id is None:
1909:             self.txt_in_report_ans.setPlainText("Please select a meeting from the left first.")
1910:             return
1911:         m = self.controller.db.get_meeting(self._selected_meeting_id)
1912:         if not m:
1913:             return
1914:         self.txt_in_report_ans.setPlainText(f"Analyzing meeting memory for '{q}'...")
1915:         res = self.controller.query_meeting_memory(f"In {m.get('title', 'Meeting')} meeting: {q}")
1916:         ans = res.get("answer", "No answer found.")
1917:         self.txt_in_report_ans.setMarkdown(ans)
```
The query string is artificially prepended with `"In {title} meeting: {q}"`, but `meeting_id=self._selected_meeting_id` is NOT passed to `query_meeting_memory()`.

#### Observation 5: Information Leakage on Zero-Match Scoped Queries
In `src/voxlocal/engine/meeting_intelligence.py` (lines 853–868):
```python
853:         if not citations:
854:             avail_titles = ", ".join(f"**{m.get('title')}**" for m in all_meetings[:4])
855:             ans = (
856:                 f"### 🔍 No Direct Matches Found\n\n"
857:                 f"I searched across your recorded meetings but could not find explicit mentions matching **'{question}'**.\n\n"
858:                 f"**Available recorded meetings:** {avail_titles}\n\n"
859:                 f"**Tips to find what you need:**\n"
860:                 f"• Ask for an executive recap: *'Summarize the key points from {all_meetings[0].get('title')}'*\n"
861:                 f"• Search by broader keywords: *'audio'*, *'record'*, *'speaker'*\n"
862:                 f"• Check the **Reports & Intelligence** tab for full verbatim transcripts."
863:             )
864:             suggestions = [
865:                 f"Summarize {all_meetings[0].get('title')}",
866:                 "What decisions were made?",
867:                 "List all action items"
868:             ]
869:             return ans, suggestions
```
When a query is scoped to a specific meeting and no matches exist, it lists the titles of 4 unrelated meetings from `all_meetings[:4]` and suggests asking about `all_meetings[0]`.

#### Observation 6: Catalog Truncation Causes Scoped Query Fallback to Unrelated Meetings
In `src/voxlocal/engine/meeting_intelligence.py` (lines 148, 843–849, 925–932, 986–993):
```python
148:             all_meetings = self.db.list_meetings(limit=250)
...
843:         scoped_meeting = None
844:         if target_meeting_id:
845:             for m in all_meetings:
846:                 if m.get("id") == target_meeting_id:
847:                     scoped_meeting = m
848:                     break
...
927:         if scoped_meeting:
928:             explicit_actions = self.db.list_action_items(scoped_meeting.get("id"))
929:         else:
930:             for m in all_meetings[:3]:
931:                 explicit_actions.extend(self.db.list_action_items(m.get("id")))
```
If `target_meeting_id` is older than the top 250 meetings, `scoped_meeting` is `None`. The action items and decisions synthesizers silently pull and return records from `all_meetings[:3]` (three completely unrelated meetings).

#### Observation 7: Pre-Meeting Briefing Cites Generic Titles Without Anchor Timestamps
In `src/voxlocal/engine/meeting_intelligence.py` (lines 598–614):
```python
598:         if prior_decisions:
599:             for d in prior_decisions[:6]:
600:                 meeting_tag = f" *[{d.get('meeting_title', '')}]*" if d.get("meeting_title") else ""
601:                 cert = f" ({d.get('certainty')})" if d.get('certainty') else ""
602:                 briefing_lines.append(f"- **{d.get('decision', '')}**{meeting_tag}{cert}")
...
608:                 owner = act.get("owner") or "Unassigned"
609:                 due = f" · Due: {act.get('due_date')}" if act.get("due_date") else ""
610:                 meeting_tag = f" ({act.get('meeting_title', '')})" if act.get("meeting_title") else ""
611:                 briefing_lines.append(f"- [ ] **[{owner}]** {act.get('description', '')}{due}{meeting_tag}")
```
Items cite `meeting_title`, but discard `d.get('created_at')`, `d.get('timestamp_sec')`, `act.get('created_at')`, and `act.get('timestamp_sec')`. Furthermore, if an action was completed in a subsequent session, `list_action_items(status="pending")` on the older session revives it as an open deliverable.

---

### 4.2. Logic Chain

1. **Dashboard KPI Defect Chain**:
   - Step 1: `_refresh_meeting_dashboard_stats` calls `list_meetings(limit=1000)` and `list_action_items(limit=5000)` (Observation 1).
   - Step 2: Because `list_meetings` does not filter by status (`status = 'completed'`), unfinalized, aborted, or crashed meetings are included in `total_m` and `total_sec`.
   - Step 3: When a meeting with existing completed actions is reprocessed, `clear_meeting_outcomes` preserves completed items. Minor LLM rephrasing bypasses exact string deduplication in `add_action_item`, inserting duplicate pending records.
   - Step 4: Because `decisions` has no status column, reversed or rejected decisions accumulate indefinitely in `total_decisions`.
   - Conclusion: Dashboard KPIs are non-atomic, drift upon reprocessing, include invalid session states, and truncate when catalog limits are reached.

2. **Presentation Template & Export Privacy Chain**:
   - Step 1: `_render_templated_summary` uses fallback strings that assert affirmative facts ("milestone sign-off completed", "Terms accepted", "statement of work") when sections are empty (Observation 2).
   - Step 2: When users export reports via `_export_selected`, the code invokes `TranscriptExporter.export_to_file(m)`, ignoring the template entirely (Observation 3).
   - Step 3: `TranscriptExporter` dumps `m['notes']` and verbatim transcripts to file without redaction or confirmation safeguards.
   - Conclusion: Templates currently fabricate facts in absence of data, while exports completely bypass template presentation and leak internal-only notes to external audiences.

3. **Meeting-Scoped Ask Vox Isolation Failure Chain**:
   - Step 1: In-report search invokes `query_meeting_memory` with a string prefix instead of passing `meeting_id` (Observation 4).
   - Step 2: Regex title extraction is brittle; if regex fails or matches another title substring, query executes globally across all meetings.
   - Step 3: Even when `meeting_id` is explicitly passed, if no matches are found, `_synthesize_answer` explicitly outputs the titles of 4 unrelated meetings and suggests them to the user (Observation 5).
   - Step 4: When `target_meeting_id` is not among the top 250 meetings, `scoped_meeting` resolves to `None`, triggering a fallback that injects action items and decisions from 3 unrelated meetings (Observation 6).
   - Conclusion: Meeting-scoped Ask Vox violates the zero-leakage invariant under multiple realistic workflows.

4. **Pre-Meeting Briefing Integrity Chain**:
   - Step 1: `generate_pre_meeting_briefing` retrieves items by querying individual past meetings for `status='pending'` (Observation 7).
   - Step 2: It does not check subsequent meetings for completion evidence or resolution notes.
   - Step 3: Citations discard timestamp anchors and session timestamps, providing only generic titles.
   - Step 4: Database lacks a `risks` table; blockers cannot be cross-referenced or tracked to resolution.
   - Conclusion: Pre-meeting briefings lack forensic source citations and revive stale tasks due to the absence of cross-meeting reconciliation.

---

### 4.3. Caveats

- **No Live Schema Changes**: In accordance with strictly read-only rules for Milestones 0–2, no modifications have been made to SQLite schemas, source files, or tests.
- **Model Non-Determinism**: While local LLM inference uses `temperature=0.3`, exact wording generated across regenerations varies slightly, reinforcing the necessity of semantic/hash-based task reconciliation over exact string matching.
- **Standalone Client Dependencies**: Desktop UI behavior was inspected via source analysis and headless Qt test harness; Windows Explorer file reveal commands (`explorer /select`) rely on standard Win32 shell integration.

---

### 4.4. Conclusion & Data-Ownership Map

#### Data-Ownership Map

| Entity / Domain | Source Table(s) | Primary Controller / Service | UI Component(s) | Verification Suite |
|-----------------|-----------------|------------------------------|-----------------|--------------------|
| Dashboard KPIs | `meetings`, `action_items`, `decisions` | `LocalDatabase`, `AppController` | `VoxMeetingDashboardHeader`, `MainWindow._refresh_meeting_dashboard_stats` | `test_meeting_studio_phase5_enterprise_ux.py` |
| Presentation Templates | Memory-only formatting over `meetings`, `action_items`, `decisions` | `MainWindow._render_templated_summary` | `MainWindow.combo_report_template`, `MainWindow.txt_summary` | `test_meeting_studio_phase5_advanced_intelligence.py` |
| File Exporter & Privacy | `meetings`, `transcript_segments`, `action_items`, `decisions` | `TranscriptExporter`, `LocalMeetingSummarizer` | `MainWindow._export_selected`, Export Context Menus | `test_download_transcript.py`, `test_reports_workspace_redesign.py` |
| Pre-Meeting Briefings | `meetings`, `action_items`, `decisions` (needs `risks`) | `MeetingIntelligenceEngine.generate_pre_meeting_briefing` | `VoxPreMeetingBriefingDialog`, `MainWindow.btn_pre_briefing` | `test_meeting_studio_phase5_advanced_intelligence.py` |
| Cross-Meeting Comparison | `meetings`, `action_items`, `decisions` | `MeetingIntelligenceEngine.compare_meetings` | `VoxMeetingComparisonDialog` | `test_meeting_studio_phase5_advanced_intelligence.py` |
| Ask Vox Semantic Memory | `transcript_segments`, `meetings`, `action_items`, `decisions` | `LocalDatabase.search_meeting_memory`, `MeetingIntelligenceEngine.query/stream`, `LocalLLMEngine` | `MainWindow.txt_ask_query`, `MainWindow.combo_ask_meeting_scope`, `citations_list` | `test_meeting_intelligence.py`, `test_ask_vox_streaming_and_branding.py` |

#### Architectural Remediation Blueprint (For Milestone 3 Implementation Plan)

1. **Dashboard KPI Remediation**:
   - Add `LocalDatabase.get_dashboard_kpis() -> Dict[str, Any]` executing native SQL aggregate queries:
     - `SELECT COUNT(*), COALESCE(SUM(duration_sec), 0.0) FROM meetings WHERE status = 'completed'`
     - `SELECT COUNT(*) FROM action_items a JOIN meetings m ON a.meeting_id = m.id WHERE a.status = 'pending' AND m.status = 'completed'`
     - `SELECT COUNT(*) FROM decisions d JOIN meetings m ON d.meeting_id = m.id WHERE m.status = 'completed'`
   - Replace in-memory array scans and hardcoded limits in `_refresh_meeting_dashboard_stats()`.

2. **Report Templates & Privacy Remediation**:
   - Eliminate all fact-fabricating fallback strings in `_render_templated_summary()`. If no data exists, output explicit neutral notices: `"No decisions recorded for this session."`, `"No commitments tracked."`.
   - Connect active presentation template to `_export_selected()`: pass templated text into exporter or provide an option to export rendered template versus raw transcript.
   - For `Client Review` exports:
     - Filter out user notes (`meeting["notes"]`).
     - Scan content for internal sensitivity markers (`[internal]`, `confidential`, `margin`, `budget`).
     - Display a Review & Confirmation Safeguard dialog before writing the export file if sensitive items or raw transcripts are detected.

3. **Pre-Meeting Briefing & Blocker Remediation**:
   - Enhance briefing citations: format each decision and action with `[Meeting Title - YYYY-MM-DD at MM:SS]`.
   - Cross-meeting reconciliation: check later meetings for completion or supersession before surfacing old tasks. If a task was marked completed in any later session, omit it from open carry-over.
   - Introduce structured blocker/risk tracking (or extract Stage 6 risks from `mom_json` in `meetings.summary`) and verify whether subsequent evidence marks them resolved.

4. **Meeting-Scoped Ask Vox Remediation**:
   - Fix `_submit_in_report_ask_query`: pass `meeting_id=self._selected_meeting_id` directly to `self.controller.query_meeting_memory()`.
   - Strict Isolation Post-Filter: in `_retrieve_citations`, guarantee that when `target_meeting_id` is not None, `citations = [c for c in citations if c.get('meeting_id') == target_meeting_id]`.
   - Zero-Match Quarantine: when `target_meeting_id` is set and `citations` is empty, output: `"### 🔍 No Matches in {scoped_meeting_title}\n\nNo mentions matching '{question}' were found in this meeting."` Do NOT leak `all_meetings[:4]` or suggest other meetings.
   - Remove catalog truncation fallback: fetch scoped meeting directly via `db.get_meeting(target_meeting_id)` instead of searching `all_meetings[:250]`. Never fall back to `all_meetings[:3]` when scoped.
   - Sync UI dropdown: when executing meeting-scoped queries via suggested chips or in-report capsules, synchronize `combo_ask_meeting_scope`.

---

### 4.5. Verification Method

To verify these findings and reproduce the defects in an isolated environment (Milestone 2):

1. **Verify Baseline Test Inventory**:
   ```powershell
   python scratch/run_suites_clean.py
   ```
   *Expected*: Verifies the reported 12 suites (98 tests) pass on clean isolated test fixtures.

2. **Reproduce Scoped Ask Vox Information Leakage**:
   - Create isolated test database with two meetings:
     - Meeting 1: "Project Alpha Strategy" (Content: "Budget is 100k")
     - Meeting 2: "Confidential Mergers" (Content: "Target acquisition code name Falcon")
   - Execute query on Meeting 1: `intel.query("What is the code name?", meeting_id=meeting1_id)`.
   - *Observation*: Answer outputs `"Available recorded meetings: Confidential Mergers"` and suggests `"Summarize Confidential Mergers"`, proving cross-meeting leakage.

3. **Reproduce In-Report Ask Vox Meeting ID Drop**:
   - Inspect `src/voxlocal/ui/main_window.py:1915`.
   - Call `_submit_in_report_ask_query()` on Meeting 1 with query `"Falcon"`.
   - *Observation*: Returns findings from Meeting 2 because `meeting_id` was not passed.

4. **Reproduce Fact Fabrication in Client Review Template**:
   - Create empty meeting with no summary or decisions.
   - Call `_render_templated_summary(meeting, "👥 Client Review", [], [])`.
   - *Observation*: Output asserts `"Client review and milestone sign-off completed."` and `"• Terms accepted as discussed."`.

5. **Reproduce Dashboard KPI Inconsistency**:
   - Insert an action item with `status = 'cancelled'`.
   - Call `_refresh_meeting_dashboard_stats()`.
   - *Observation*: `kpi_actions` displays `"1"` open action despite item being cancelled.

---
*Report completed and verified read-only. File generated at `d:\Milind\Voice Assistant\.agents\teamwork\spec_miner_m1_cross\handoff.md`.*

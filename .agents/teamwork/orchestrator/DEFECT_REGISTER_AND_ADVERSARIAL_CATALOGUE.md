# Defect Register & Adversarial Test Catalogue (Revised)

**Project**: VOX Meeting Studio Phase 6  
**Status**: Revised Specification for User Approval  
**Operating Mode**: STRICTLY READ-ONLY EMPIRICAL REPRODUCTION (100% Isolated Ephemeral Testing)  

---

## 1. Pre-Implementation Defects & Test Suite Defect Register

In addition to business logic defects in application source code, read-only exploration and baseline audits identified two critical test-suite defects:

| Defect ID | Component | Defect Description | Forensic Evidence & Root Cause | Impact | Planned Remediation |
|:---|:---|:---|:---|:---|:---|
| **DEF-PRE-01** | Test Suite Storage Isolation | `tests/test_meeting_studio_enterprise_phase2.py` and `tests/test_cross_feature_coexistence.py` write test meetings to `data/voxlocal.db` | `self.config.data_dir = test_dir` assigns arbitrary attribute on dataclass. `self.config.storage_dir` remains default `""`, resolving to production `data/voxlocal.db`. Created Meeting IDs 67–100 and SR IDs 12–27. | Pollutes shared development database with 0-duration test sessions. | Add `@property def data_dir` and setter to `AppConfig` in `config.py` pointing directly to `storage_dir`. |
| **DEF-PRE-02** | Test Suite Sequential Dependency | Sequential run of `scratch/run_suites_clean.py` fails on `test_pre_meeting_briefing_dialog` (97/98 pass) | Consecutively running `test_meeting_comparison_dialog` and `test_pre_meeting_briefing_dialog` both call `_copy_slack()` -> `QApplication.clipboard().setText()`. Win32 OLE locks clipboard without event loop pumping, causing second read to return `""`. | Prevents clean 98/98 sequential test qualification. | Add `app.processEvents()` after clipboard writes, clear clipboard in `tearDown()`, and use flushed retry helper. |
| **DEF-PRE-03** | Test Suite Mock Path Leakage | Stray folder `MagicMock\mock.stop()\capture_diagnostics.json` created in workspace root | In `controller.py:1466`, `Path(video_path).with_name(...)` evaluates `Path(MagicMock)` as string `"MagicMock/mock.stop()"` and writes to CWD. | Leaves stray folders in repository root. | Guard `isinstance(video_path, (str, Path))` in `controller.py` before writing diagnostics. |

---

## 2. Complete Adversarial Test Catalogue (34 Scenarios)

All 34 scenarios were evaluated and empirically confirmed in isolated ephemeral test environments (`scratch/test_adversarial_m2_reproduction.py`):

| Scenario ID | Focus Area | Title / Requirement | Input Scenario | Expected Outcome | Actual (Defective) Outcome | Classification | Severity |
|:---|:---|:---|:---|:---|:---|:---|:---|
| **ADV-01** | FA 1 | Order-Independent Meeting Comparison | Call `compare_meetings(m1, m2)` vs `(m2, m1)` where m1 is older | Identical chronological `#1 vs #2` comparison and delta tracking | Positional order inverts header to `#2 vs #1` and lists older topics as new in B | **confirmed defect** | High |
| **ADV-02** | FA 1 | 7-State Decision Evolution Tracking | Sequential meetings with evolving decisions (Postgres -> SQLite) | Classify evolution states (`superseded`, `reversed`, `confirmed`) | Only lists side-by-side decisions with zero evolution states | **missing mandatory capability** | High |
| **ADV-03** | FA 1 | Chronological Accuracy in Slack Export | Swap combo selection in Meeting Comparison dialog | Correctly label latest meeting decisions as active | Labels older meeting decisions as `*[New/Active]*` in latest meeting | **confirmed defect** | High |
| **ADV-04** | FA 1 | Relational Decision Schema Support | Check `decisions` table structure | Contains status, category, evolution_relationship, supersedes_id, is_manual | Missing all mandatory columns | **missing mandatory capability** | High |
| **ADV-05** | FA 1 | Structured Decision Categorization | Extract decision with LLM category `Confirmed` | Store category in dedicated relational column | Coerced into string prefix `[Confirmed] ...` inside rationale | **missing mandatory capability** | Medium |
| **ADV-06** | FA 2 | User-Edited Action Item Preservation | Pending action item edited by user (custom text, assignee, date) | Retained across report regeneration | Permanently deleted by `clear_meeting_outcomes` | **confirmed defect** | **Critical** |
| **ADV-07** | FA 2 | Manual Action Item Preservation | User manually adds new action item via UI dialog | Retained across report regeneration | Permanently deleted by `clear_meeting_outcomes` | **confirmed defect** | **Critical** |
| **ADV-08** | FA 2 | Decision Preservation Across Re-Analysis | Meeting with curated strategic decisions | Decisions preserved or merged with AI updates | Unconditionally erased by `DELETE FROM decisions` | **confirmed defect** | **Critical** |
| **ADV-09** | FA 2 | Enterprise Task Lifecycle States | Toggle action item status | Support pending, completed, cancelled, reopened, blocked | Binary toggle hardcoded between pending and completed | **missing mandatory capability** | Medium |
| **ADV-10** | FA 2 | Live Flag Task Preservation | User clicks "⚡ Flag Action" during recording | Flagged action item preserved for review | Deleted upon meeting finalization by `clear_meeting_outcomes` | **confirmed defect** | High |
| **ADV-11** | FA 2 | Live Bookmark Persistence | User clicks "🔖 Bookmark" during recording | Bookmark saved to SQLite with timestamp | Only appended to UI text feed; lost on window close | **missing mandatory capability** | Medium |
| **ADV-12** | FA 3 | Calendar Due Date Resolution | Utterance: "Milind will deploy by Friday" | Resolved to verified calendar date (`YYYY-MM-DD`) | Literal text `"By friday"` stored directly in SQLite | **AI-grounding risk** | High |
| **ADV-13** | FA 3 | Timezone-Aware Timestamp Policy | Inspect meetings timestamp and config | Timezone stored with timestamp; user timezone policy | Naive local timestamp stored; zero timezone configuration | **missing mandatory capability** | Medium |
| **ADV-14** | FA 3 | Explicit Temporal Uncertainty Retention | Utterance: "revisit in a couple of weeks" | Retain explicit uncertainty reason | Coerced to `"Not specified"` with zero metadata | **AI-grounding risk** | Medium |
| **ADV-15** | FA 4 | Unbounded Dashboard KPI Calculation | Database with > 1,000 meetings or > 5,000 tasks | Exact SQL count across entire database | Truncated at 1,000 meetings and 5,000 tasks | **data-integrity risk** | High |
| **ADV-16** | FA 4 | Validated Meeting Status in KPIs | Database has completed, crashed, and recording meetings | Only completed meetings counted in totals | Crashed and in-progress meetings counted in total talk time | **data-integrity risk** | High |
| **ADV-17** | FA 4 | Consistent KPI Metric Definitions | Database has cancelled action item | UI and DB analytics report consistent pending count | UI reports 1 open action; DB analytics reports 0 | **logical inconsistency** | Medium |
| **ADV-18** | FA 4 | Re-Analysis Task Deduplication | Completed task rephrased slightly by AI on regeneration | Matched to existing completed task | Inserts duplicate pending task; pending count inflates | **data-integrity risk** | High |
| **ADV-19** | FA 4 | Active Decisions KPI Filtering | Meeting 1 decides DynamoDB; Meeting 2 reverses to SQLite | KPI reflects active/confirmed decisions | Accumulates all historical rows indefinitely | **data-integrity risk** | Medium |
| **ADV-20** | FA 5 | Truthful Client Review Summary | Meeting has empty summary and decisions | Truthfully states no summary recorded | Fabricates: "milestone sign-off completed", "terms accepted" | **confirmed defect** | High |
| **ADV-21** | FA 5 | Truthful Executive Brief Summary | Meeting has empty summary | Truthfully states no executive overview | Fabricates: "Discussion concluded successfully without blockers" | **confirmed defect** | Medium |
| **ADV-22** | FA 5 | Truthful Troubleshooting Summary | Meeting has empty decisions and actions | Truthfully states no diagnostics logged | Fabricates: "Root cause investigation underway" | **confirmed defect** | Medium |
| **ADV-23** | FA 5 | File Export Template Fidelity | User selects Client Review template and exports Markdown | File formatted per Client Review template | Bypasses template completely; exports raw internal format | **confirmed defect** | High |
| **ADV-24** | FA 5 | Client Review Internal Notes Privacy | Meeting contains confidential internal note | Redacts note and presents confirmation safeguard | Verbatim export of confidential notes under `## Notes` | **confirmed defect** | **Critical** |
| **ADV-25** | FA 6 | Honest Stepper Error Reporting | Exception occurs in finalization worker | Stepper highlights error stage with retry action | Emits `"idle"`, hides stepper, completely masking failure | **confirmed defect** | **Critical** |
| **ADV-26** | FA 6 | Stage 2 Transcript Preservation | Stage 2 succeeds; Stage 3 diarization crashes | Stage 2 transcript committed and preserved | Transcript lost permanently; 0 segments in SQLite | **confirmed defect** | **Critical** |
| **ADV-27** | FA 6 | Cooperative Pipeline Cancellation | User cancels long-running finalization | Pipeline halts cleanly at stage boundary | Worker runs uncontrollably to completion or crash | **missing mandatory capability** | Medium |
| **ADV-28** | FA 7 | Anchor Citations in Briefings | Pre-meeting briefing on topic | Citations include meeting date and anchor timestamps | Discards timestamps and session dates | **AI-grounding risk** | Medium |
| **ADV-29** | FA 7 | Evidence-Grounded Task Carry-Over | Meeting 1 task completed in Meeting 2 | Excludes task from open carry-over | Blindly revives task as pending carry-over work | **logical inconsistency** | High |
| **ADV-30** | FA 7 | Dedicated Risks & Blockers Tracking | Inspect schema for risks / blockers | Dedicated table with status tracking | No table exists; risks trapped in unstructured text | **missing mandatory capability** | Medium |
| **ADV-31** | FA 8 | In-Report Ask Vox Direct ID Passing | Query title with brackets: `[PROD] Database Outage` | Passes `meeting_id` directly to query engine | Drops `meeting_id`; regex fails, leaking to global search | **confirmed defect** | **Critical** |
| **ADV-32** | FA 8 | Zero-Match Scoped Query Privacy | Scoped query on topic with no matches | Reports no matches in targeted meeting | Displays: "Available recorded meetings: <4 unrelated titles>" | **confirmed defect** | High |
| **ADV-33** | FA 8 | Scoped Query Catalog Scalability | Query meeting older than top 250 sessions | Fetches meeting directly via `get_meeting()` | Resolves to `None`; returns actions from 3 unrelated meetings | **confirmed defect** | High |
| **ADV-34** | FA 8 | UI Meeting Scope Dropdown Cap | User has 60 recorded meetings | All 60 meetings available in dropdown | Hardcoded cap at 50 meetings | **UX ambiguity** | Medium |

# Task Assignment: Spec Miner M1 Cross-Meeting & Privacy

## Assigned Role
- **Archetype**: teamwork_spec_miner
- **Working Directory**: `d:\Milind\Voice Assistant\.agents\teamwork\spec_miner_m1_cross\`
- **Mandate**: Strict Read-Only Specification & Logic Investigation for Cross-Meeting Intelligence, Privacy & Scoping (Focus Areas 4, 5, 7, 8).
- **Reference**: `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`

## Instructions
1. Strictly read-only investigation — do not modify any source files or data.
2. Investigate codebase architecture, queries, controllers, services, UI components, and prompts for:
   - **Focus Area 4: Dashboard KPI Integrity**:
     - How metrics (Meetings Recorded, Total Talk Time, Open Action Items, Strategic Decisions) are calculated.
     - Database query reconciliation and preventing duplicate counts across reprocessed meetings or status changes.
   - **Focus Area 5: Report Templates & Export Privacy**:
     - Ensure templates alter presentation without altering facts.
     - How internal-only information is identified in Client Review exports.
     - Review and confirmation safeguards for audience-sensitive information.
   - **Focus Area 7: Cross-Meeting Intelligence & Pre-Meeting Briefings**:
     - Citing supporting source meetings for all key briefing items.
     - Ensuring completed tasks or resolved blockers are never revived without new evidence.
   - **Focus Area 8: Meeting-Scoped Ask Vox Queries**:
     - Ensuring meeting-scoped Ask Vox queries return zero content or citations from unrelated meetings.
     - Query vector / keyword search filtering mechanisms.
3. Deliver a comprehensive analysis and data-ownership mapping report to:
   `d:\Milind\Voice Assistant\.agents\teamwork\spec_miner_m1_cross\handoff.md`.


## 2026-11-02T00:53:07Z
You are teamwork_spec_miner assigned to Milestone 1: Cross-Meeting Intelligence, Privacy, Dashboard & Query Scoping (Focus Areas 4, 5, 7, 8).
Your working directory is: d:\Milind\Voice Assistant\.agents\teamwork\spec_miner_m1_cross\
Your reference document: d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md

CRITICAL CONSTRAINTS:
- STRICTLY READ-ONLY: Do NOT modify any source files, database schemas, records, config, or user data.
- Operate strictly local-only with no cloud AI or telemetry.

TASKS:
1. Read d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md first.
2. Investigate codebase architecture, queries, controllers, services, UI components, and prompts across the following focus areas:
   - **Focus Area 4: Dashboard KPI Integrity**:
     - How metrics (Meetings Recorded, Total Talk Time, Open Action Items, Strategic Decisions) are calculated and reconciled against database queries.
     - Mechanisms to prevent duplicate counts across reprocessed meetings or status changes.
   - **Focus Area 5: Report Templates & Export Privacy**:
     - Ensuring templates alter presentation without altering facts.
     - Identification of internal-only information in Client Review exports.
     - Review and confirmation safeguards for audience-sensitive information.
   - **Focus Area 7: Cross-Meeting Intelligence & Pre-Meeting Briefings**:
     - Pre-meeting briefing generation: citation of supporting source meetings for key items.
     - Ensuring completed tasks or resolved blockers are never revived without new evidence.
   - **Focus Area 8: Meeting-Scoped Ask Vox Queries**:
     - Strict isolation: verifying meeting-scoped Ask Vox queries return zero content or citations from unrelated meetings.
     - Review RAG / search filtering logic.
3. Map data ownership, identify root causes of existing defects, inconsistencies, or risks.
4. Record your detailed findings, code references, schema structures, and recommendations in:
   `d:\Milind\Voice Assistant\.agents\teamwork\spec_miner_m1_cross\handoff.md`.
5. Update your `progress.md` with your status and timestamp.
6. When complete, send a message to orchestrator with your summary and handoff report path.

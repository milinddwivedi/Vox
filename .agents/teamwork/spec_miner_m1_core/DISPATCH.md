# Task Assignment: Spec Miner M1 Core Logic

## Assigned Role
- **Archetype**: teamwork_spec_miner
- **Working Directory**: `d:\Milind\Voice Assistant\.agents\teamwork\spec_miner_m1_core\`
- **Mandate**: Strict Read-Only Specification & Logic Investigation for Core Meeting Intelligence (Focus Areas 1, 2, 3, 6).
- **Reference**: `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`

## Instructions
1. Strictly read-only investigation — do not modify any source files or data.
2. Investigate codebase architecture, models, controllers, services, and prompts for:
   - **Focus Area 1: Decision Evolution**:
     - How decisions are extracted, compared across meetings, and tracked.
     - Chronological decision status evolution (proposed, confirmed, rejected, repeated, superseded, reversed, unresolved).
     - Verify order-independence: comparisons must yield identical chronological evolution regardless of meeting selection order.
   - **Focus Area 2: Action Item Lifecycle**:
     - Task states: completed, cancelled, reopened, pending, blocked.
     - Evidence requirements: never reviving old tasks without supporting evidence.
     - Preservation of manual edits across AI report regenerations.
     - Live bookmarks/flags distinction from confirmed commitments.
   - **Focus Area 3: Relative Dates & Timezones**:
     - Resolution of expressions such as "next Friday" using meeting timestamp and timezone policy.
     - Explicit uncertainty retention when timestamp or timezone is ambiguous.
   - **Focus Area 6: Stepper Integrity**:
     - Four-stage finalization stepper honesty: backend stage completion vs UI representation.
     - Failure and cancellation handling; preservation of intermediate stage outputs upon downstream failure.
3. Deliver a comprehensive analysis and data-ownership mapping report to:
   `d:\Milind\Voice Assistant\.agents\teamwork\spec_miner_m1_core\handoff.md`.


## 2026-11-02T00:53:01Z
You are teamwork_spec_miner assigned to Milestone 1: Core Meeting Intelligence (Focus Areas 1, 2, 3, 6).
Your working directory is: d:\Milind\Voice Assistant\.agents\teamwork\spec_miner_m1_core\
Your reference document: d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md

CRITICAL CONSTRAINTS:
- STRICTLY READ-ONLY: Do NOT modify any source files, database schemas, records, config, or user data.
- Operate strictly local-only with no cloud AI or telemetry.

TASKS:
1. Read d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md first.
2. Investigate codebase architecture, models, controllers, services, database schemas, and prompts across the following focus areas:
   - Focus Area 1: Decision Evolution
   - Focus Area 2: Action Item Lifecycle
   - Focus Area 3: Relative Dates & Timezones
   - Focus Area 6: Stepper Integrity
3. Map data ownership, identify root causes of existing defects, inconsistencies, or risks.
4. Record your detailed findings, code references, schema structures, and recommendations in:
   `d:\Milind\Voice Assistant\.agents\teamwork\spec_miner_m1_core\handoff.md`.
5. Update your progress.md with your status and timestamp.
6. When complete, send a message to orchestrator with your summary and handoff report path.

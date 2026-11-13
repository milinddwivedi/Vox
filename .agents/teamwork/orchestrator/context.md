# Orchestration Context: VOX Meeting Studio Phase 6

## Project Directives
- **Phase**: Phase 6 Enterprise Business Logic Integrity, Cross-Meeting Intelligence & Trustworthiness.
- **Constraints**:
  - Milestones 0–2: Strictly read-only. No modifying source files, database schemas, records, config, or user data.
  - User Approval Gate: Prior to Milestone 3, submit discovery report, defect register, adversarial test catalogue (>=25 scenarios), and file-level implementation plan for explicit user approval.
  - Baseline Test Requirement: Verify 98/98 baseline tests via `scratch/run_suites_clean.py` executed against isolated test data.
  - Local-Only Guarantee: Zero cloud AI, zero telemetry, zero external data leakage.
  - Verification & Release: Independent verification, full regression pass, database backward-compatibility check, and standalone Windows executable rebuild (`dist/VoxLocal/VoxLocal.exe`).

## Focus Areas
1. Decision evolution (chronological consistency, supersession, rejection, confirmation).
2. Action item lifecycle (state machine, manual edit persistence, bookmarks distinction).
3. Relative dates (meeting timestamp grounding, timezone verification, explicit uncertainty).
4. Dashboard KPI integrity (exact query reconciliation, duplicate avoidance).
5. Report templates & export privacy (internal-only safeguards, client review filtering).
6. Stepper integrity (stage completion honesty, cancellation handling, intermediate artifact preservation).
7. Cross-meeting intelligence (pre-meeting briefings citation, blocker resolution truth).
8. Meeting-scoped Ask Vox queries (zero cross-meeting leakage or citations).

## Key Paths
- Working directory: `d:\Milind\Voice Assistant`
- Authoritative Request: `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`
- Orchestrator Directory: `d:\Milind\Voice Assistant\.agents\teamwork\orchestrator\`

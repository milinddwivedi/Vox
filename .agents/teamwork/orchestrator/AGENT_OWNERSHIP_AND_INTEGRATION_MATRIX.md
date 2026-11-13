# Agent Ownership & Integration Matrix (Final)

**Project**: VOX Meeting Studio Phase 6  
**Status**: Final Corrected Specification for User Approval  
**Operating Mode**: Strictly Partitioned Exclusive File Boundaries  

---

## 1. Exclusive Implementation File Ownership

Every file is owned by exactly one worker. No concurrent edits to shared files are permitted.

| Worker ID | Assigned Role | Exclusive File Ownership | Core Scope & Responsibilities |
|:---|:---|:---|:---|
| **Worker 1** | Storage & Schema Lead | `src/voxlocal/storage/database.py`<br>`src/voxlocal/core/config.py` | 1. Schema migration `_migrate_v6` (`meetings.timezone` NULL for legacy; `decisions` canonical `evolution_relationship` and `supersedes_id ON DELETE RESTRICT`; `action_items.due_date_resolution`; `meeting_markers`; `risks_and_blockers`).<br>2. WAL-safe backup creation (`create_verified_snapshot`) and integrity verification.<br>3. Universal non-destructive `clear_meeting_outcomes` protecting user-created and user-edited items across all 5 statuses and referenced decisions.<br>4. Canonical SQL KPI calculations for open tasks (`status IN ('pending', 'blocked', 'reopened')`).<br>5. `AppConfig` timezone default. |
| **Worker 2** | Intelligence & RAG Lead | `src/voxlocal/engine/meeting_intelligence.py`<br>`src/voxlocal/engine/llm_engine.py` | 1. Canonical chronological normalization in `compare_meetings`.<br>2. Decoupled 7-state decision evolution matcher using canonical `evolution_relationship`. Earlier decisions preserved intact.<br>3. Structured relative date resolution with JSON `due_date_resolution` and explicit uncertainty.<br>4. Pre-meeting briefings with anchor citations (date + `HH:MM:SS`).<br>5. Ask Vox RAG isolation (direct ID lookup without 250 truncation, zero leakage on zero-match, post-retrieval quarantine). |
| **Worker 3** | Pipeline & State Lead | `src/voxlocal/core/controller.py` | 1. Intermediate Stage 2 transcript persistence before Stage 3 diarization.<br>2. Honest error signal emission (no deceptive `"idle"` on exception).<br>3. Cooperative cancellation event check (`_cancel_finalization_event`).<br>4. Guard diagnostics path against `MagicMock` string conversions. |
| **Worker 4** | UI & Presentation Lead | `src/voxlocal/ui/main_window.py`<br>`src/voxlocal/ui/components.py` | 1. Direct `meeting_id` passing for in-report search.<br>2. Truthful presentation templates (remove fact-fabrication fallbacks).<br>3. Direct database KPI widget updates using canonical open definition (`status IN ('pending', 'blocked', 'reopened')`).<br>4. Persistent live markers UI ("⚡ Flag Action", "🔖 Bookmark").<br>5. Slack export chronological accuracy (no unconditional event-loop re-entry in production clipboard handler). |
| **Worker 5** | Exporter & Privacy Lead | `src/voxlocal/storage/exporter.py` | 1. Template-aware file export (Markdown, TXT).<br>2. Client Review safe-content approach: automatic suppression of internal `notes`.<br>3. Interactive export preview with sensitive keyword warning scanning and explicit user confirmation safeguard. |
| **Worker 6** | **QA & Integration Lead** | `tests/test_phase6_enterprise_integrity.py`<br>`tests/test_phase6_migration_and_compatibility.py`<br>`tests/test_meeting_studio_phase5_advanced_intelligence.py`<br>`tests/test_meeting_studio_enterprise_phase2.py`<br>`tests/test_cross_feature_coexistence.py` | 1. Correct test fixtures in `test_meeting_studio_enterprise_phase2.py` and `test_cross_feature_coexistence.py` to use `self.config.storage_dir = str(test_dir)`, permanently preventing live database pollution.<br>2. Resolve Win32 clipboard sequential test order race in `test_meeting_studio_phase5_advanced_intelligence.py` via test `tearDown()` clipboard clearing/event flushing and bounded retry helper in test code (no weakened assertions).<br>3. Build and execute `test_phase6_enterprise_integrity.py` (34 adversarial scenarios).<br>4. Build and execute `test_phase6_migration_and_compatibility.py` on isolated database copies.<br>5. Execute full 12-suite regression run (98/98 sequential pass).<br>6. Rebuild and verify standalone Windows executable (`dist/VoxLocal/VoxLocal.exe`). |

---

## 2. Integration Coordination & Gate Requirements

1. **Integration Ordering**:
   - Track 1 (Storage & Schema) and Track 6 (Test Fixture corrections) establish the verified database contracts and test isolation.
   - Tracks 2, 3, 4, and 5 implement domain intelligence, pipeline integrity, UI truthfulness, and export privacy.
   - Track 6 executes isolated migration tests, the 34-scenario adversarial suite, full regression suite qualification, and release build verification.
2. **Read-Only Holding Mode**:
   Zero implementation workers will be dispatched until explicit user approval of this final plan is received.

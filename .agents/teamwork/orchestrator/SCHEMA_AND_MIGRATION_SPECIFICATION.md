# Schema & Migration Specification: Phase 6 Enterprise Business Logic Integrity (Final)

**Project**: VOX Meeting Studio Phase 6  
**Status**: Final Corrected Specification for User Approval  
**Operating Mode**: Development & Test Database (Local SQLite)  

---

## 1. Architectural & Safety Principles

1. **Development/Test Scope**:
   `data/voxlocal.db` is the local pre-release development database. Golden benchmark meetings (IDs 64 & 65) and acoustic assets (> 600 MB) are strictly preserved.
2. **Zero Inferred Historical Information**:
   Legacy meetings retain `timezone = NULL` and `utc_offset_min = NULL`. Zero assumed UTC or invented timezones.
   Legacy decisions default strictly to `status = 'unclassified'` and `evolution_relationship = 'new'`. Zero invented categories or statuses.
3. **Canonical Field Naming**:
   The evolutionary relationship across meetings is named strictly **`evolution_relationship`** (`'new'`, `'repeated'`, `'superseded'`, `'reversed'`). The deprecated term `evolution_state` is completely eliminated.
4. **Permanent Decision Chain Preservation**:
   Earlier decisions referenced by later decisions via `supersedes_id` must NEVER be deleted. The historical record and supporting quotes remain permanent.
5. **Universal Action Item Preservation**:
   Every user-created (`is_manual = 1`) or user-edited (`user_edited = 1`) action item is protected from deletion during regeneration across **all five statuses**: `'pending'`, `'completed'`, `'cancelled'`, `'blocked'`, and `'reopened'`.
6. **Canonical Open Action Definition**:
   An open action item is defined as: `status IN ('pending', 'blocked', 'reopened')`.
   - `pending`: Open (standard active commitment)
   - `blocked`: Open (active commitment with separate blocked indicator)
   - `reopened`: Open (reactivated commitment)
   - `completed`: Closed
   - `cancelled`: Excluded from open work (inactive / dropped)
7. **Complete Risk vs Blocker Model**:
   Table `risks_and_blockers` distinguishes future uncertainties (`risk`) from current progress impediments (`blocker`). Resolved items are permanently retained with `resolved_at` timestamps.

---

## 2. Table-by-Table Schema Specification

### 2.1 Table: `meetings`
Tracks recorded and imported meeting sessions.

| Column | Type | Constraints | Description & Legacy Semantics |
|:---|:---|:---|:---|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Unique meeting identifier |
| `title` | TEXT | NOT NULL | Meeting title |
| `start_time` | TEXT | NOT NULL | Local timestamp string (`%Y-%m-%d %H:%M:%S`) |
| `end_time` | TEXT | NULL | Local completion timestamp |
| `duration_sec` | REAL | DEFAULT 0.0 | Total duration in seconds |
| `status` | TEXT | NOT NULL DEFAULT 'completed' | `'recording'`, `'transcribing'`, `'completed'`, `'interrupted'`, `'error'` |
| `audio_path` | TEXT | NULL | Path to audio file or recording directory |
| `summary` | TEXT | DEFAULT '' | AI-generated / structured meeting summary |
| `notes` | TEXT | DEFAULT '' | User-entered internal/scratch notes |
| `user_edited` | INTEGER | DEFAULT 0 | 1 if summary was manually edited |
| `folder_id` | INTEGER | NULL REFERENCES folders(id) | Organization folder |
| `engagement_score` | REAL | DEFAULT 85.0 | Meeting engagement score |
| `created_at` | TEXT | NOT NULL | Record creation timestamp |
| **`timezone`** | **TEXT** | **NULL** | **NEW.** IANA identifier (e.g. `'America/New_York'`). **`NULL` for all legacy records.** Configured IANA string for new recordings. |
| **`utc_offset_min`** | **INTEGER** | **NULL** | **NEW.** Local offset from UTC in minutes. **`NULL` for all legacy records.** Computed offset for new recordings. |

---

### 2.2 Table: `decisions`
Tracks decisions made within and across meetings with decoupled status, evolution, and provenance.

| Column | Type | Constraints | Description & Legacy Semantics |
|:---|:---|:---|:---|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Unique decision identifier |
| `meeting_id` | INTEGER | NOT NULL REFERENCES meetings(id) ON DELETE CASCADE | Source meeting ID |
| `decision` | TEXT | NOT NULL | Decision statement text |
| `rationale` | TEXT | DEFAULT '' | Supporting rationale or discussion context |
| `timestamp_sec` | REAL | DEFAULT 0.0 | Media offset in seconds |
| `created_at` | TEXT | NOT NULL | Record creation timestamp |
| **`status`** | **TEXT** | **NOT NULL DEFAULT 'unclassified' CHECK(status IN ('unclassified', 'proposed', 'confirmed', 'rejected', 'unresolved'))** | **NEW.** Current business status. **Legacy records default strictly to `'unclassified'`** (zero invented status). |
| **`category`** | **TEXT** | **NOT NULL DEFAULT 'General'** | **NEW.** Categorization: `'Strategic'`, `'Technical'`, `'Product'`, `'Budget'`, `'Process'`, `'General'`. |
| **`evolution_relationship`** | **TEXT** | **NOT NULL DEFAULT 'new' CHECK(evolution_relationship IN ('new', 'repeated', 'superseded', 'reversed'))** | **NEW (Canonical Field Name).** Historical relationship to earlier meetings. Legacy records default to `'new'`. |
| **`supersedes_id`** | **INTEGER** | **NULL REFERENCES decisions(id) ON DELETE RESTRICT** | **NEW.** Pointer to earlier decision being superseded or reversed. `ON DELETE RESTRICT` ensures historical chain can never be broken. |
| **`evidence_quote`** | **TEXT** | **DEFAULT ''** | **NEW.** Verbatim transcript quote serving as evidence anchor. |
| **`is_manual`** | **INTEGER** | **DEFAULT 0** | **NEW.** 1 if manually entered by user. |
| **`user_edited`** | **INTEGER** | **DEFAULT 0** | **NEW.** 1 if decision text, rationale, or status was edited by user. |

#### Decision Chain Preservation Rule:
A decision row that is referenced by any other decision's `supersedes_id` is an active historical anchor and **must never be deleted**. In `clear_meeting_outcomes()`, decisions are only deleted if `is_manual = 0 AND user_edited = 0 AND id NOT IN (SELECT supersedes_id FROM decisions WHERE supersedes_id IS NOT NULL)`.

---

### 2.3 Table: `action_items`
Tracks commitments and deliverables across the full lifecycle.

| Column | Type | Constraints | Description & Legacy Semantics |
|:---|:---|:---|:---|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Unique action item identifier |
| `meeting_id` | INTEGER | NOT NULL REFERENCES meetings(id) ON DELETE CASCADE | Source meeting ID |
| `description` | TEXT | NOT NULL | Task description |
| `owner` | TEXT | DEFAULT 'Unassigned' | Task assignee |
| `due_date` | TEXT | DEFAULT '' | Human-readable deadline string |
| **`status`** | **TEXT** | **NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'completed', 'cancelled', 'reopened', 'blocked'))** | **UPDATED.** Canonical task state. Legacy records retain existing `'pending'` or `'completed'`. |
| `timestamp_sec` | REAL | DEFAULT 0.0 | Media offset in seconds |
| `created_at` | TEXT | NOT NULL | Creation timestamp |
| **`due_date_resolution`** | **TEXT** | **DEFAULT ''** | **NEW.** JSON string containing structured relative date resolution metadata (schema below). |
| **`evidence_quote`** | **TEXT** | **DEFAULT ''** | **NEW.** Verbatim quote supporting commitment. |
| **`is_manual`** | **INTEGER** | **DEFAULT 0** | **NEW.** 1 if manually entered by user. |
| **`user_edited`** | **INTEGER** | **DEFAULT 0** | **NEW.** 1 if description, owner, due date, or status was edited or changed by user. |

#### Exact JSON Schema for `due_date_resolution`:
```json
{
  "original_expression": "by next Friday",
  "resolved_date": "2026-11-06",
  "meeting_timestamp": "2026-10-31 02:01:00",
  "timezone_used": null,
  "resolution_status": "deterministic",
  "uncertainty_reason": null
}
```
- `original_expression` (string): Raw spoken or extracted phrase.
- `resolved_date` (string | null): ISO date (`YYYY-MM-DD`) when determinable; `null` if ambiguous.
- `meeting_timestamp` (string): Anchor session timestamp used as reference.
- `timezone_used` (string | null): IANA timezone string used during evaluation, or `null` if naive/legacy.
- `resolution_status` (string): `'deterministic'`, `'ambiguous'`, or `'unresolved'`.
- `uncertainty_reason` (string | null): Explanatory reason when date cannot be determined with certainty.

---

### 2.4 Table: `meeting_markers`
Tracks real-time live events during recording without polluting confirmed business commitments.

```sql
CREATE TABLE IF NOT EXISTS meeting_markers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL,
    marker_type TEXT NOT NULL CHECK(marker_type IN ('flag_action', 'bookmark')),
    label TEXT NOT NULL,
    timestamp_sec REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_markers_meeting ON meeting_markers(meeting_id);
```

---

### 2.5 Table: `risks_and_blockers`
Tracks project uncertainties and active progress impediments with distinct semantics and permanent historical retention.

```sql
CREATE TABLE IF NOT EXISTS risks_and_blockers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL,
    item_type TEXT NOT NULL CHECK(item_type IN ('risk', 'blocker')),
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    impact TEXT NOT NULL DEFAULT 'medium' CHECK(impact IN ('low', 'medium', 'high', 'critical')),
    status TEXT NOT NULL CHECK(status IN ('identified', 'mitigated', 'accepted', 'closed', 'active', 'resolved', 'reopened')),
    mitigation_or_resolution TEXT DEFAULT '',
    linked_action_id INTEGER NULL REFERENCES action_items(id) ON DELETE SET NULL,
    linked_decision_id INTEGER NULL REFERENCES decisions(id) ON DELETE SET NULL,
    source_timestamp_sec REAL DEFAULT 0.0,
    evidence_quote TEXT DEFAULT '',
    is_manual INTEGER DEFAULT 0,
    user_edited INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    resolved_at TEXT NULL,
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_risks_meeting ON risks_and_blockers(meeting_id);
CREATE INDEX IF NOT EXISTS idx_risks_type_status ON risks_and_blockers(item_type, status);
```

#### Type-Specific Status Constraints:
- When `item_type = 'risk'`: Allowed statuses are `'identified'`, `'mitigated'`, `'accepted'`, `'closed'`.
- When `item_type = 'blocker'`: Allowed statuses are `'active'`, `'resolved'`, `'reopened'`.
- **Permanent Historical Retention**: Resolved risks and blockers are **never deleted**. When a risk is mitigated or a blocker resolved, its `status` is updated, and `resolved_at` is set to the current timestamp. They remain in the database for auditing and historical sprint reviews.

---

## 3. SQLite Migration Execution & Safe Recovery

### 3.1 Migration Sequence (`_migrate_v6`)
1. **Pre-Migration Snapshot**:
   Call `create_verified_snapshot()`. Forces WAL checkpoint (`PRAGMA wal_checkpoint(TRUNCATE)`), creates SQLite backup via online backup API, and validates `PRAGMA integrity_check`.
2. **Version Check**:
   Read `PRAGMA user_version`. If `>= 6`, exit cleanly (idempotent).
3. **Atomic Schema Migration**:
   ```sql
   BEGIN IMMEDIATE;
   
   -- 1. Alter meetings table
   ALTER TABLE meetings ADD COLUMN timezone TEXT NULL;
   ALTER TABLE meetings ADD COLUMN utc_offset_min INTEGER NULL;
   
   -- 2. Alter decisions table (Canonical evolution_relationship)
   ALTER TABLE decisions ADD COLUMN status TEXT NOT NULL DEFAULT 'unclassified';
   ALTER TABLE decisions ADD COLUMN category TEXT NOT NULL DEFAULT 'General';
   ALTER TABLE decisions ADD COLUMN evolution_relationship TEXT NOT NULL DEFAULT 'new';
   ALTER TABLE decisions ADD COLUMN supersedes_id INTEGER NULL REFERENCES decisions(id) ON DELETE RESTRICT;
   ALTER TABLE decisions ADD COLUMN evidence_quote TEXT DEFAULT '';
   ALTER TABLE decisions ADD COLUMN is_manual INTEGER DEFAULT 0;
   ALTER TABLE decisions ADD COLUMN user_edited INTEGER DEFAULT 0;
   
   -- 3. Alter action_items table (due_date_resolution explicitly added)
   ALTER TABLE action_items ADD COLUMN due_date_resolution TEXT DEFAULT '';
   ALTER TABLE action_items ADD COLUMN evidence_quote TEXT DEFAULT '';
   ALTER TABLE action_items ADD COLUMN is_manual INTEGER DEFAULT 0;
   ALTER TABLE action_items ADD COLUMN user_edited INTEGER DEFAULT 0;
   
   -- 4. Create new tables
   CREATE TABLE IF NOT EXISTS meeting_markers (...);
   CREATE TABLE IF NOT EXISTS risks_and_blockers (...);
   
   -- 5. Update user_version
   PRAGMA user_version = 6;
   
   COMMIT;
   ```
4. **Post-Migration Verification**:
   Execute `PRAGMA foreign_key_check` and `PRAGMA integrity_check`.

### 3.2 Failure & Recovery
- Automatic `ROLLBACK` on exception.
- Failed database preserved at `data/snapshots/voxlocal_failed_v6_<timestamp>.db`.
- Verified pre-migration snapshot preserved at `data/snapshots/voxlocal_pre_v6_<timestamp>.db`.
- **Zero blind auto-restoration over newer records.** Application stops safely and alerts developer.
- All migration unit tests execute on isolated temporary database copies.

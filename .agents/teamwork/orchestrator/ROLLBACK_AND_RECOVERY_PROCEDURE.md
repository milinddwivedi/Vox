# Rollback & Recovery Procedure: Development & Test Database

**Project**: VOX Meeting Studio Phase 6  
**Status**: Revised Specification for User Approval  
**Operating Mode**: Proportionate Development/Test SQLite Governance  

---

## 1. Operating Context & Safety Principles

1. **Development/Test Scope**:
   VOX Meeting Studio is a pre-release application. While avoiding excessive enterprise disaster-recovery overhead, development evidence, golden benchmark recordings (IDs 64 & 65), and existing testing assets must be safeguarded with absolute discipline.
2. **Prohibition of Blind Restores**:
   The system must **never** automatically restore an old backup over a database that may contain newer valid records or test results.
3. **Dual-State Preservation on Error**:
   If a migration or schema update encounters an error, both the pre-migration snapshot AND the post-error state must be preserved for forensic analysis.
4. **Mandatory Snapshot Integrity Verification**:
   No migration step may proceed unless the pre-migration snapshot has been explicitly verified using `PRAGMA integrity_check`.

---

## 2. Pre-Migration Snapshot Procedure

Before applying Phase 6 migrations, `LocalDatabase` executes the following sequence:

```
[Start Migration Request]
         │
         ▼
[1. Force WAL Checkpoint]  ──► Executes PRAGMA wal_checkpoint(TRUNCATE) to merge all journal pages
         │
         ▼
[2. Online SQLite Backup]  ──► Uses sqlite3 backup API to stream pages to data/snapshots/voxlocal_pre_v6_<timestamp>.db
         │
         ▼
[3. Integrity Verification] ──► Opens snapshot in read-only mode and runs PRAGMA integrity_check
         │
         ├──(FAIL)──► Abort immediately. Log error. Zero changes made.
         │
         ▼ (PASS)
[4. Begin Migration Transaction]
```

---

## 3. Atomic Migration & Rollback Behavior

All schema modifications (adding columns to `meetings`, `decisions`, `action_items`; creating `meeting_markers` and `risks_and_blockers`) are wrapped in an atomic SQLite transaction:

```sql
BEGIN IMMEDIATE;
-- Migration statements...
UPDATE schema_versions SET version = 6; -- or PRAGMA user_version = 6
COMMIT;
```

### On Exception / Failure:
1. **Immediate SQLite Rollback**:
   The database engine issues `ROLLBACK`, restoring in-memory tables to the state prior to the transaction.
2. **Dual-State Artifact Preservation**:
   - The verified pre-migration backup remains untouched at:
     `data/snapshots/voxlocal_pre_v6_<timestamp>.db`.
   - The current database state (in case disk writes or partial locks occurred) is copied to:
     `data/snapshots/voxlocal_failed_v6_<timestamp>.db`.
3. **Safe Stop & User Notification**:
   - The migration halts immediately.
   - A detailed exception trace is logged to `data/logs/migration_failure.log`.
   - The application displays an explicit dialog:
     `"Database migration to Phase 6 encountered an error. All changes have been rolled back safely. Your pre-migration snapshot is preserved at data/snapshots/..."`
   - The application does NOT attempt destructive re-migration or overwrite data.

---

## 4. Manual Rollback & Inspection Procedures

If a developer desires to inspect or restore a pre-migration snapshot manually:

### Option A: Inspect the Pre-Migration Snapshot (Read-Only)
```powershell
.venv\Scripts\python.exe -c "
import sqlite3
conn = sqlite3.connect('file:data/snapshots/voxlocal_pre_v6_YYYYMMDD_HHMMSS.db?mode=ro', uri=True)
print('Integrity:', conn.execute('PRAGMA integrity_check').fetchall())
print('Meetings count:', conn.execute('SELECT COUNT(*) FROM meetings').fetchone()[0])
conn.close()
"
```

### Option B: Manual Reversion to Snapshot
1. Ensure the application is closed.
2. Archive the current database:
   ```powershell
   Move-Item -Path "data/voxlocal.db" -Destination "data/voxlocal_archived.db"
   ```
3. Copy the verified snapshot:
   ```powershell
   Copy-Item -Path "data/snapshots/voxlocal_pre_v6_YYYYMMDD_HHMMSS.db" -Destination "data/voxlocal.db"
   ```
4. Verify checksum and startup.

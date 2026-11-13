<h2>VOX Database Migration &amp; Recovery Manual</h2>

<p><strong>Standard:</strong> Complete operational runbook for database migrations, online WAL snapshots, integrity checks, and disaster recovery.<br/>
<strong>Production Database:</strong> <code>data/voxlocal.db</code> (SQLite 3 WAL Mode).<br/>
<strong>Current Schema Version:</strong> 6.</p>

<hr/>

<h3>1. Schema Migration History</h3>
<ul>
  <li><strong>v0 -&gt; v1:</strong> Initial schema (meetings, transcript_segments, custom_vocabulary).</li>
  <li><strong>v1 -&gt; v2:</strong> Added folders, dictation_history, action_items.</li>
  <li><strong>v2 -&gt; v3:</strong> Added screen_recordings table and video metadata.</li>
  <li><strong>v3 -&gt; v4:</strong> Added decisions table and meeting tags.</li>
  <li><strong>v4 -&gt; v5:</strong> Added summary markdown columns and Ask Vox semantic cache.</li>
  <li><strong>v5 -&gt; v6 (Phase 6):</strong>
    <ul>
      <li>Preserved legacy meeting timezone neutrality (<code>timezone = NULL</code>, <code>utc_offset_min = NULL</code>).</li>
      <li>Added decoupled decision evolution: <code>evolution_relationship TEXT DEFAULT 'new'</code> with check constraint.</li>
      <li>Added foreign key relationship: <code>supersedes_id INTEGER NULL REFERENCES decisions(id) ON DELETE RESTRICT</code>.</li>
      <li>Added structured JSON column <code>due_date_resolution TEXT DEFAULT ''</code> to <code>action_items</code>.</li>
      <li>Created dedicated relational tables: <code>meeting_markers</code> and <code>risks_and_blockers</code>.</li>
      <li>Set <code>PRAGMA user_version = 6;</code>.</li>
    </ul>
  </li>
</ul>

<hr/>

<h3>2. Online WAL Backup Procedure</h3>
<p>To safely back up <code>data/voxlocal.db</code> while VOX is running:</p>
<pre><code>import sqlite3
from pathlib import Path

def backup_database(src_path="data/voxlocal.db", dest_path="data/snapshots/backup.db"):
    with sqlite3.connect(src_path) as src:
        // Checkpoint WAL pages into main DB file
        src.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        with sqlite3.connect(dest_path) as dest:
            src.backup(dest)
    
    // Verify backup integrity
    with sqlite3.connect(dest_path) as dest:
        res = dest.execute("PRAGMA integrity_check;").fetchone()
        assert res[0] == "ok", f"Backup corrupted: {res[0]}"
    print(f"Verified backup saved to {dest_path}")
</code></pre>

<hr/>

<h3>3. Disaster Recovery &amp; Rollback Procedure</h3>
<ol>
  <li><strong>Stop the Application:</strong> Ensure no <code>VoxLocal.exe</code> or Python process holds a lock on the database files.</li>
  <li><strong>Verify Snapshot Integrity:</strong> Locate the latest verified pre-migration snapshot in <code>data/snapshots/voxlocal_pre_v6_&lt;timestamp&gt;.db</code>. Run <code>PRAGMA integrity_check;</code> on the snapshot.</li>
  <li><strong>Safe Restoration:</strong>
    <pre><code>Copy-Item "data/snapshots/voxlocal_pre_v6_&lt;timestamp&gt;.db" "data/voxlocal.db" -Force
Remove-Item "data/voxlocal.db-wal", "data/voxlocal.db-shm" -ErrorAction SilentlyContinue
</code></pre>
  </li>
  <li><strong>Validation:</strong> Launch VOX with <code>--smoke-test-ms 2000</code> to verify clean recovery and table connectivity.</li>
</ol>

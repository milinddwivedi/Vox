<h2>VOX Architecture Decision Records (ADRs)</h2>

<p><strong>Standard:</strong> Architecture Decision Records covering major design, schema, and operational decisions across Phases 6 through 15.<br/>
<strong>Architecture:</strong> Local-first, privacy-preserving desktop application for Windows 11.</p>

<hr/>

<h3>ADR-001: Schema Version 6 &amp; Canonical Decision Evolution Model</h3>
<ul>
  <li><strong>Date:</strong> 2026-11-02</li>
  <li><strong>Status:</strong> ACCEPTED &amp; IMPLEMENTED</li>
  <li><strong>Context:</strong> Previous iterations conflated business status with cross-meeting decision evolution, leading to ambiguous classification when comparing multiple meetings.</li>
  <li><strong>Decision:</strong> Decouple <code>evolution_relationship</code> (<code>new</code>, <code>repeated</code>, <code>superseded</code>, <code>reversed</code>) from business <code>status</code> (<code>unclassified</code>, <code>proposed</code>, <code>confirmed</code>, <code>rejected</code>, <code>unresolved</code>). Enforce relational linkage via <code>supersedes_id INTEGER NULL REFERENCES decisions(id) ON DELETE RESTRICT</code> under <code>PRAGMA foreign_keys = ON;</code>.</li>
  <li><strong>Consequences:</strong> Earlier anchor decisions cannot be deleted while referenced by newer meetings. Full audit provenance is mathematically guaranteed in SQLite.</li>
</ul>

<hr/>

<h3>ADR-002: Universal User-Edit &amp; Action Item Preservation</h3>
<ul>
  <li><strong>Date:</strong> 2026-11-02</li>
  <li><strong>Status:</strong> ACCEPTED &amp; IMPLEMENTED</li>
  <li><strong>Context:</strong> Re-running meeting finalisation or MoM regeneration previously executed bulk deletes on pending action items, wiping user-created or manual priority modifications.</li>
  <li><strong>Decision:</strong> Update <code>clear_meeting_outcomes()</code> to strictly delete only machine-generated, unmodified items (<code>is_manual = 0 AND user_edited = 0 AND status = 'pending'</code>). Any item modified by the user or transitioned into <code>completed</code>, <code>cancelled</code>, <code>blocked</code>, or <code>reopened</code> is permanently preserved across regenerations.</li>
  <li><strong>Consequences:</strong> Zero data loss for human commitments during AI reprocessing.</li>
</ul>

<hr/>

<h3>ADR-003: Deterministic Relative Date Resolution with Explicit Ambiguity</h3>
<ul>
  <li><strong>Date:</strong> 2026-11-02</li>
  <li><strong>Status:</strong> ACCEPTED &amp; IMPLEMENTED</li>
  <li><strong>Context:</strong> Relative deadlines like "by Friday" or "in 2 weeks" required anchoring to meeting timestamps, while ambiguous terms like "sometime" or "next sprint" previously caused LLM date hallucinations.</li>
  <li><strong>Decision:</strong> Implement a deterministic calendar arithmetic parser anchored to <code>meeting.created_at</code>. For ambiguous temporal phrases, store <code>resolved_date = None</code> with <code>resolution_status = "ambiguous"</code> and explicit <code>uncertainty_reason</code> in <code>action_items.due_date_resolution</code> JSON.</li>
  <li><strong>Consequences:</strong> Accurate calendar deadlines for grounded phrases; zero synthetic date fabrication for ambiguous commitments.</li>
</ul>

<hr/>

<h3>ADR-004: Scoped Ask Vox Retrieval Quarantine</h3>
<ul>
  <li><strong>Date:</strong> 2026-11-02</li>
  <li><strong>Status:</strong> ACCEPTED &amp; IMPLEMENTED</li>
  <li><strong>Context:</strong> In-report Ask Vox queries searched the global 250-meeting catalog, causing context leakage across unrelated confidential client sessions.</li>
  <li><strong>Decision:</strong> In-report Ask Vox queries must pass explicit <code>meeting_id</code> directly from UI to engine. The query fetches the scoped meeting directly from SQLite (bypassing catalog caps) and applies post-retrieval quarantine to guarantee that 100% of candidate chunks originate from the target session. Zero-match responses truthfully indicate lack of information in the active meeting without leaking other indexed titles.</li>
  <li><strong>Consequences:</strong> Complete client meeting privacy; no cross-meeting context leakage in reports.</li>
</ul>

<hr/>

<h3>ADR-005: Online WAL-Safe Backup Snapshots</h3>
<ul>
  <li><strong>Date:</strong> 2026-11-02</li>
  <li><strong>Status:</strong> ACCEPTED &amp; IMPLEMENTED</li>
  <li><strong>Context:</strong> Hot file-copying of active SQLite databases running in WAL mode risks corrupted snapshots if transactions are mid-commit.</li>
  <li><strong>Decision:</strong> Implement <code>create_verified_snapshot()</code> using <code>PRAGMA wal_checkpoint(TRUNCATE);</code> followed by the native SQLite Online Backup API (<code>src_conn.backup(dest_conn)</code>) and post-copy <code>PRAGMA integrity_check;</code>.</li>
  <li><strong>Consequences:</strong> 100% verifiable, transactionally consistent backups without taking the application offline.</li>
</ul>

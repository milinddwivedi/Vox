import sqlite3
import tempfile
from pathlib import Path
from voxlocal.storage.database import LocalDatabase

temp_dir = tempfile.mkdtemp(prefix="vox_audit_fk_")
db_path = Path(temp_dir) / "test.db"

db = LocalDatabase(db_path)
mid = db.create_meeting("Test FK")

# Add decision 1
d1_id = db.add_decision(mid, "Original Decision", status="confirmed")

# Add decision 2 superseding decision 1
d2_id = db.add_decision(
    mid,
    "Updated Decision",
    status="confirmed",
    evolution_relationship="superseded",
    supersedes_id=d1_id,
)

print(f"Created decisions: d1={d1_id}, d2={d2_id}")

# Attempt to delete d1 directly using SQL with foreign_keys ON
try:
    with db._connect() as conn:
        conn.execute("DELETE FROM decisions WHERE id = ?", (d1_id,))
    print("ERROR: Deletion of d1 succeeded without restriction!")
except sqlite3.IntegrityError as e:
    print(f"PASS: Foreign key constraint blocked deletion as expected: {e}")

# Attempt to delete d1 using db.delete_decision
ok = db.delete_decision(d1_id)
print(f"delete_decision(d1_id) returned: {ok}")

# Verify d1 is still in database
d1 = db.get_decision(d1_id)
print(f"d1 still exists: {d1 is not None}")

import tempfile
from pathlib import Path
from voxlocal.storage.database import LocalDatabase

temp_dir = tempfile.mkdtemp(prefix="vox_audit_pres_")
db_path = Path(temp_dir) / "test.db"

db = LocalDatabase(db_path)
mid = db.create_meeting("Test Outcomes")

# Add action items in all 5 statuses with user_edited=1 and is_manual=1
statuses = ["pending", "completed", "cancelled", "blocked", "reopened"]
manual_ids = []
user_edited_ids = []
auto_pending_ids = []

for s in statuses:
    # 1. Manual item
    m_id = db.add_action_item(mid, f"Manual {s}", status=s, is_manual=1, user_edited=0)
    manual_ids.append(m_id)
    
    # 2. User-edited item
    u_id = db.add_action_item(mid, f"User-edited {s}", status=s, is_manual=0, user_edited=1)
    user_edited_ids.append(u_id)

# 3. Auto-generated pending items (should be cleared)
a1 = db.add_action_item(mid, "Auto pending 1", status="pending", is_manual=0, user_edited=0)
a2 = db.add_action_item(mid, "Auto pending 2", status="pending", is_manual=0, user_edited=0)
auto_pending_ids.extend([a1, a2])

# Add decisions:
# - An auto decision that is supersedes target
d_target = db.add_decision(mid, "Decision Anchor", status="confirmed", is_manual=0, user_edited=0)
d_child = db.add_decision(mid, "Decision Child", status="confirmed", evolution_relationship="superseded", supersedes_id=d_target, is_manual=0, user_edited=0)

# - An auto unreferenced decision (should be cleared)
d_unref = db.add_decision(mid, "Decision Discardable", status="proposed", is_manual=0, user_edited=0)

# - A user-edited decision (should be preserved)
d_edited = db.add_decision(mid, "Decision User Edited", status="confirmed", is_manual=0, user_edited=1)

print(f"Total action items before clear: {len(db.list_action_items(meeting_id=mid))}")
print(f"Total decisions before clear: {len(db.list_decisions(meeting_id=mid))}")

# Now call clear_meeting_outcomes
db.clear_meeting_outcomes(mid, preserve_completed=True)

remaining_actions = db.list_action_items(meeting_id=mid)
remaining_action_ids = {a["id"] for a in remaining_actions}

print(f"Total action items after clear: {len(remaining_actions)}")
for m_id in manual_ids:
    assert m_id in remaining_action_ids, f"Manual item {m_id} was deleted!"
for u_id in user_edited_ids:
    assert u_id in remaining_action_ids, f"User-edited item {u_id} was deleted!"
for a_id in auto_pending_ids:
    assert a_id not in remaining_action_ids, f"Auto item {a_id} was NOT deleted!"

print("PASS: Universal action item edit protection verified across all 5 statuses!")

remaining_decisions = db.list_decisions(meeting_id=mid)
remaining_decision_ids = {d["id"] for d in remaining_decisions}

assert d_target in remaining_decision_ids, f"Anchor decision {d_target} was deleted!"
assert d_edited in remaining_decision_ids, f"User edited decision {d_edited} was deleted!"
assert d_unref not in remaining_decision_ids, f"Unreferenced decision {d_unref} was NOT deleted!"

print("PASS: Decision preservation and anchor protection verified!")

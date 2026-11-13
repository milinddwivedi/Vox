import sqlite3
import sys

sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect("file:data/voxlocal.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=== ALL MEETINGS DETAIL ===")
rows = c.execute("SELECT id, title, created_at, status, duration_sec, audio_path, video_path FROM meetings ORDER BY id").fetchall()
for r in rows:
    mid = r["id"]
    n_seg = c.execute("SELECT count(*) FROM transcript_segments WHERE meeting_id = ?", (mid,)).fetchone()[0]
    n_act = c.execute("SELECT count(*) FROM action_items WHERE meeting_id = ?", (mid,)).fetchone()[0]
    n_dec = c.execute("SELECT count(*) FROM decisions WHERE meeting_id = ?", (mid,)).fetchone()[0]
    print(f"ID {mid:3d} | '{r['title']:30s}' | {r['created_at']} | status={r['status']:12s} | dur={r['duration_sec']:7.1f}s | segs={n_seg:3d} | acts={n_act:2d} | decs={n_dec:1d} | audio={r['audio_path']} | video={r['video_path']}")

print("\n=== SCREEN RECORDINGS DETAIL ===")
sr_rows = c.execute("SELECT id, title, created_at, duration_sec, file_path, file_size_bytes FROM screen_recordings ORDER BY id").fetchall()
for sr in sr_rows:
    print(f"SR ID {sr['id']:2d} | '{sr['title']:28s}' | {sr['created_at']} | dur={sr['duration_sec']:5.1f}s | size={sr['file_size_bytes']:9,d} | path={sr['file_path']}")

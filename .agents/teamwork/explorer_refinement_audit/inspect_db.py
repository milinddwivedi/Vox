import sys
import sqlite3

sys.stdout.reconfigure(encoding='utf-8')

uri = 'file:data/voxlocal.db?mode=ro'
conn = sqlite3.connect(uri, uri=True)
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=== TABLE COUNTS ===")
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
for t in sorted(tables):
    count = c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
    print(f"{t}: {count}")

print("\n=== MEETINGS WITH NON-EMPTY SUMMARY OR DETAILS ===")
for row in c.execute("SELECT id, title, created_at, status, duration_sec, substr(summary, 1, 60) as sum_prefix FROM meetings ORDER BY id").fetchall():
    print(dict(row))

print("\n=== DECISIONS ===")
for row in c.execute("SELECT * FROM decisions").fetchall():
    print(dict(row))

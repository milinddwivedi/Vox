import os
import sys
import json
import wave
import sqlite3
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

recordings_dir = Path("data/recordings")

# Connect to DB read-only
conn = sqlite3.connect("file:data/voxlocal.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

meetings = cur.execute("SELECT id, title, created_at, duration_sec, audio_path, mic_audio_path, sys_audio_path, video_path, status FROM meetings").fetchall()
screen_recs = cur.execute("SELECT id, title, created_at, duration_sec, file_path, file_size_bytes FROM screen_recordings").fetchall()

print(f"Total meetings in DB: {len(meetings)}")
print(f"Total screen recordings in DB: {len(screen_recs)}")

# Inventory of recording directories
rec_dirs = sorted([d for d in recordings_dir.iterdir() if d.is_dir() and d.name != "screen"])
screen_dir = recordings_dir / "screen"

def get_wav_duration(p: Path):
    try:
        with wave.open(str(p), 'rb') as w:
            frames = w.getnframes()
            rate = w.getframerate()
            return frames / float(rate)
    except Exception as e:
        return None

inventory = []

print("\n=== MEETING RECORDING DIRECTORIES IN data/recordings ===")
for rd in rec_dirs:
    files = list(rd.iterdir())
    dir_info = {
        "dir_name": rd.name,
        "path": str(rd),
        "files": []
    }
    
    # Check matching DB meeting
    matching_meetings = [m for m in meetings if rd.name in (m["audio_path"] or "") or rd.name in (m["video_path"] or "")]
    
    total_size = 0
    for f in files:
        if f.is_file():
            sz = f.stat().st_size
            total_size += sz
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            ext = f.suffix.lower()
            dur = None
            if ext == ".wav":
                dur = get_wav_duration(f)
            
            f_info = {
                "name": f.name,
                "size_bytes": sz,
                "ext": ext,
                "mtime": mtime,
                "duration_sec": dur
            }
            if f.name == "capture_diagnostics.json":
                try:
                    with open(f, "r", encoding="utf-8") as jf:
                        diag = json.load(jf)
                        f_info["diagnostics_summary"] = {
                            "ok": diag.get("ok"),
                            "duration": diag.get("duration"),
                            "frames": diag.get("frames_written") or diag.get("total_frames")
                        }
                except Exception as e:
                    f_info["diagnostics_summary"] = str(e)
            dir_info["files"].append(f_info)
    
    dir_info["total_size_bytes"] = total_size
    dir_info["matched_meetings"] = [dict(m) for m in matching_meetings]
    inventory.append(dir_info)
    
    mm_str = f"Mapped DB Meeting IDs: {[m['id'] for m in matching_meetings]}" if matching_meetings else "No direct meeting path match in DB"
    print(f"\nFolder: {rd.name} | Files: {len(files)} | Size: {total_size:,} bytes | {mm_str}")
    for fi in dir_info["files"]:
        dur_str = f", dur={fi['duration_sec']:.2f}s" if fi['duration_sec'] is not None else ""
        print(f"  - {fi['name']} ({fi['size_bytes']:,} bytes, {fi['mtime']}{dur_str})")

print("\n=== SCREEN RECORDINGS IN data/recordings/screen ===")
if screen_dir.exists():
    screen_files = sorted(list(screen_dir.glob("*.mp4")))
    print(f"Total .mp4 files in screen/: {len(screen_files)}")
    for sf in screen_files:
        sz = sf.stat().st_size
        mtime = datetime.fromtimestamp(sf.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        matching_sr = [sr for sr in screen_recs if sf.name in (sr["file_path"] or "")]
        msr_str = f"Mapped SR IDs: {[sr['id'] for sr in matching_sr]}" if matching_sr else "No DB match"
        print(f"  - {sf.name} ({sz:,} bytes, {mtime}) | {msr_str}")

# Save detailed inventory to JSON in agent folder
with open(".agents/teamwork/explorer_refinement_audit/recording_inventory.json", "w", encoding="utf-8") as out:
    json.dump(inventory, out, indent=2)

print("\nWrote detailed inventory to .agents/teamwork/explorer_refinement_audit/recording_inventory.json")

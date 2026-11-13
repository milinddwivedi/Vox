import av
import os
from PIL import Image

def extract_key_frames(video_path, out_dir, count=8):
    os.makedirs(out_dir, exist_ok=True)
    container = av.open(video_path)
    stream = next(s for s in container.streams if s.type == 'video')
    total = stream.frames or 27000
    indices = [int(i * total / count) for i in range(count)]
    saved = 0
    curr = 0
    for frame in container.decode(video=0):
        if curr in indices:
            img = frame.to_image()
            out_file = os.path.join(out_dir, f"frame_{curr:06d}.jpg")
            img.save(out_file)
            print(f"Saved {out_file} (size {img.size})")
            saved += 1
            if saved >= count:
                break
        curr += 1

extract_key_frames(
    r"D:\Milind\Voice Assistant\data\recordings\meeting_20261031_020100\meeting_screen.mp4",
    r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\frames_1031_020100",
    count=6
)

extract_key_frames(
    r"D:\Milind\Voice Assistant\External References\Teams Recording Example.mp4",
    r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\frames_teams_example",
    count=4
)

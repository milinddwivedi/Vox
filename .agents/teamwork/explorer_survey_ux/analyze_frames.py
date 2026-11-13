import os
from PIL import Image
import numpy as np

frames_dir = r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\frames'
frame_files = sorted([f for f in os.listdir(frames_dir) if f.endswith('.jpg')])

print(f"Total frames: {len(frame_files)}")

# Check resolution and sample a few frames
im0 = Image.open(os.path.join(frames_dir, frame_files[0]))
w, h = im0.size
print(f"Resolution: {w}x{h}")

# Check differences between consecutive frames to detect scene transitions / key action points
transitions = []
prev_arr = None

for i, fname in enumerate(frame_files):
    img = Image.open(os.path.join(frames_dir, fname)).convert('L').resize((160, 90))
    arr = np.array(img, dtype=np.float32)
    if prev_arr is not None:
        diff = np.mean(np.abs(arr - prev_arr))
        if diff > 5.0:  # significant change
            transitions.append((fname, diff))
    prev_arr = arr

print(f"Significant visual changes detected at {len(transitions)} frames:")
for t in transitions[:30]:
    print(f" - {t[0]}: diff {t[1]:.2f}")

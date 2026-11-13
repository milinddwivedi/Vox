import os
from PIL import Image
import numpy as np

frames_dir = r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\frames'

# Let's inspect frame 0, 8, 15, 30, 44, 50, 60
sample_times = [0, 5, 8, 15, 25, 35, 44, 48, 50, 55, 60]

for sec in sample_times:
    fname = f"frame_{sec:06.2f}s.jpg"
    fpath = os.path.join(frames_dir, fname)
    if not os.path.exists(fpath):
        continue
    img = Image.open(fpath)
    # Let's inspect dominant colors or regions
    # Check top, bottom, center
    top_region = img.crop((0, 0, img.width, 100))
    center_region = img.crop((img.width//4, img.height//4, 3*img.width//4, 3*img.height//4))
    bottom_region = img.crop((0, img.height - 100, img.width, img.height))
    
    print(f"\n--- Frame {sec:02d}s ({fname}) ---")
    print(f"Top avg RGB: {np.mean(top_region, axis=(0,1)).astype(int)}")
    print(f"Center avg RGB: {np.mean(center_region, axis=(0,1)).astype(int)}")
    print(f"Bottom avg RGB: {np.mean(bottom_region, axis=(0,1)).astype(int)}")

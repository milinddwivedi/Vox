import os
from PIL import Image
import numpy as np

frames_dir = r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\frames'
out_crops_dir = r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\crops'
os.makedirs(out_crops_dir, exist_ok=True)

# Let's inspect the bottom center and bottom right of frames 0, 5, 8, 15, 30, 44, 50, 60
for sec in [0, 5, 8, 15, 25, 30, 44, 50, 60]:
    fpath = os.path.join(frames_dir, f"frame_{sec:06.2f}s.jpg")
    if not os.path.exists(fpath): continue
    im = Image.open(fpath)
    w, h = im.size
    
    # Bottom center (typical toggle bar / flow bar position)
    bc = im.crop((w//2 - 250, h - 120, w//2 + 250, h))
    bc.save(os.path.join(out_crops_dir, f"bottom_center_{sec:02d}s.jpg"))
    
    # Bottom right (typical toast notification position)
    br = im.crop((w - 500, h - 300, w, h))
    br.save(os.path.join(out_crops_dir, f"bottom_right_{sec:02d}s.jpg"))
    
    # Top center
    tc = im.crop((w//2 - 300, 0, w//2 + 300, 150))
    tc.save(os.path.join(out_crops_dir, f"top_center_{sec:02d}s.jpg"))

print("Saved crops successfully!")

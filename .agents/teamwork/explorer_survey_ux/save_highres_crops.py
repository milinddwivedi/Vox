import os
from PIL import Image

frames_dir = r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\frames'
out_dir = r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\crops\highres'
os.makedirs(out_dir, exist_ok=True)

# Let's inspect frame 44s, 48s, 50s (Wispr Flow)
for s in [44, 48, 50]:
    im = Image.open(os.path.join(frames_dir, f"frame_{s:06.2f}s.jpg"))
    w, h = im.size
    # Center region around bottom
    crop = im.crop((w//2 - 250, h - 160, w//2 + 250, h - 10))
    crop.save(os.path.join(out_dir, f"wispr_flow_bottom_bar_{s:02d}s.png"))

# Let's inspect frame 08s, 24s, 36s (VOX)
for s in [8, 24, 36]:
    im = Image.open(os.path.join(frames_dir, f"frame_{s:06.2f}s.jpg"))
    w, h = im.size
    crop_bar = im.crop((w//2 - 250, h - 160, w//2 + 250, h - 10))
    crop_bar.save(os.path.join(out_dir, f"vox_bottom_bar_{s:02d}s.png"))
    
    crop_popup = im.crop((w - 520, h - 280, w - 10, h - 10))
    crop_popup.save(os.path.join(out_dir, f"vox_bottom_right_popup_{s:02d}s.png"))

print("Saved high-res comparison crops!")

import os
from PIL import Image

frames_dir = r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\frames'
out_dir = r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\crops\full'
os.makedirs(out_dir, exist_ok=True)

# Let's save smaller resized versions of key frames
for sec in [0, 5, 8, 15, 25, 30, 44, 48, 50, 55, 60, 64]:
    fname = f"frame_{sec:06.2f}s.jpg"
    fpath = os.path.join(frames_dir, fname)
    if os.path.exists(fpath):
        im = Image.open(fpath)
        im_resized = im.resize((960, 540))
        im_resized.save(os.path.join(out_dir, f"frame_{sec:02d}s_half.jpg"), quality=85)
print("Saved half-scale frames!")

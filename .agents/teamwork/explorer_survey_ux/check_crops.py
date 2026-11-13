import os
from PIL import Image
import numpy as np

crops_dir = r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\crops'
files = sorted(os.listdir(crops_dir))

for f in files:
    fpath = os.path.join(crops_dir, f)
    im = Image.open(fpath)
    arr = np.array(im)
    # Check brightness / variance
    std_rgb = np.std(arr, axis=(0,1))
    mean_rgb = np.mean(arr, axis=(0,1))
    # If high variance, there is distinct UI
    if np.max(std_rgb) > 20:
        print(f"{f}: mean={mean_rgb.astype(int)}, std={std_rgb.astype(int)} -> UI PRESENT")
    else:
        print(f"{f}: flat/uniform")

import os
from PIL import Image
import numpy as np

def analyze_img(path):
    img = Image.open(path)
    arr = np.array(img)
    h, w, c = arr.shape
    mean = np.mean(arr, axis=(0, 1))
    # Check top bar, bottom bar, center
    top_bar = arr[:50, :, :]
    bottom_bar = arr[-50:, :, :]
    center = arr[h//4:3*h//4, w//4:3*w//4, :]
    print(f"--- {os.path.basename(path)} ({w}x{h}) ---")
    print(f"  Overall RGB: {mean.astype(int)}")
    print(f"  Top bar RGB: {np.mean(top_bar, axis=(0,1)).astype(int)}")
    print(f"  Bottom bar RGB: {np.mean(bottom_bar, axis=(0,1)).astype(int)}")
    print(f"  Center RGB: {np.mean(center, axis=(0,1)).astype(int)}")

for d in [r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\frames_1031_020100",
          r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\frames_teams_example"]:
    print(f"Directory: {d}")
    for f in sorted(os.listdir(d)):
        if f.endswith(".jpg"):
            analyze_img(os.path.join(d, f))

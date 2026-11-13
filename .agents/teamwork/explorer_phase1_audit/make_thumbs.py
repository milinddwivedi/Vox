import os
from PIL import Image
import numpy as np

def sample_slices(img_path, prefix):
    img = Image.open(img_path)
    w, h = img.size
    # Save a thumbnail
    thumb = img.resize((w // 4, h // 4))
    thumb.save(f"{prefix}_thumb.png")
    print(f"Saved {prefix}_thumb.png")

sample_slices(r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\frames_teams_example\frame_000000.jpg", r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\teams_ex_0")
sample_slices(r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\frames_teams_example\frame_000173.jpg", r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\teams_ex_173")
sample_slices(r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\frames_1031_020100\frame_004516.jpg", r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\m1031_4516")

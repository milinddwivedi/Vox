from PIL import Image
import numpy as np
import os

frames_dir = r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\frames'

def analyze_frame_details(sec):
    fname = f"frame_{sec:06.2f}s.jpg"
    fpath = os.path.join(frames_dir, fname)
    if not os.path.exists(fpath): return
    im = Image.open(fpath)
    w, h = im.size
    
    # Check bottom center: [w//2 - 220 : w//2 + 220, h - 80 : h - 10]
    bc = im.crop((w//2 - 220, h - 80, w//2 + 220, h - 10))
    arr_bc = np.array(bc)
    
    # Check bottom right: [w - 460 : w - 20, h - 220 : h - 20]
    br = im.crop((w - 460, h - 220, w - 20, h - 20))
    arr_br = np.array(br)
    
    # Top title bar of active window
    tt = im.crop((20, 20, 600, 70))
    
    print(f"\n================ Frame {sec:02d}s ================")
    print(f"Bottom Center mean RGB: {np.mean(arr_bc, axis=(0,1)).astype(int)}, std: {np.std(arr_bc, axis=(0,1)).astype(int)}")
    print(f"Bottom Right mean RGB: {np.mean(arr_br, axis=(0,1)).astype(int)}, std: {np.std(arr_br, axis=(0,1)).astype(int)}")
    
    # Check if there is dark pill in bottom center (mean RGB < 50, but std > 10)
    if np.mean(arr_bc) < 60 and np.std(arr_bc) > 10:
        print(" -> Bottom Center has DARK PILL / TOGGLE BAR")
    elif np.mean(arr_bc) > 150:
        print(" -> Bottom Center has LIGHT BACKGROUND")
        
    # Check if bottom right has white popup card (mean RGB > 200)
    if np.mean(arr_br) > 200:
        print(" -> Bottom Right has WHITE TOAST POPUP (VOX Prompt Popup)")

for s in [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 64]:
    analyze_frame_details(s)

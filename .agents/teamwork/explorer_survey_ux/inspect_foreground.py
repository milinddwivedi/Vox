from PIL import Image
import numpy as np

def inspect_crop(filename):
    im = Image.open(f'd:/Milind/Voice Assistant/.agents/teamwork/explorer_survey_ux/crops/{filename}')
    arr = np.array(im)
    print(f"\n=== {filename} ({im.width}x{im.height}) ===")
    
    # Find bounding box of foreground elements (pixels significantly different from background)
    # Background is often the edge color (e.g. corner pixel)
    bg_color = arr[0, 0]
    diff = np.sqrt(np.sum((arr.astype(float) - bg_color.astype(float))**2, axis=2))
    fg_mask = diff > 25
    
    if np.any(fg_mask):
        y_indices, x_indices = np.where(fg_mask)
        min_x, max_x = np.min(x_indices), np.max(x_indices)
        min_y, max_y = np.min(y_indices), np.max(y_indices)
        w_box = max_x - min_x + 1
        h_box = max_y - min_y + 1
        print(f"Foreground bounding box: x=[{min_x}..{max_x}] (w={w_box}), y=[{min_y}..{max_y}] (h={h_box})")
        
        # Sample center of foreground
        cy = (min_y + max_y) // 2
        cx = (min_x + max_x) // 2
        center_color = arr[cy, cx]
        print(f"Center pixel color: RGB {center_color}")
    else:
        print("No foreground detected against corner background.")

for f in ["bottom_center_00s.jpg", "bottom_center_05s.jpg", "bottom_center_08s.jpg", 
          "bottom_center_15s.jpg", "bottom_center_30s.jpg", "bottom_center_44s.jpg", 
          "bottom_center_50s.jpg", "bottom_center_60s.jpg", "bottom_right_05s.jpg", "bottom_right_08s.jpg"]:
    inspect_crop(f)

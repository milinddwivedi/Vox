from PIL import Image
import numpy as np

def inspect_bar(name):
    im = Image.open(f'd:/Milind\Voice Assistant/.agents/teamwork/explorer_survey_ux/crops/highres/{name}')
    arr = np.array(im)
    print(f"\n--- {name} ({im.width}x{im.height}) ---")
    
    # Bounding box of pill
    # Find rows where there is a dark container (luminance < 40 or border around 48)
    lum = 0.299*arr[:,:,0] + 0.587*arr[:,:,1] + 0.114*arr[:,:,2]
    # In Wispr Flow, background is #1f1f1f or #000, desktop is wallpaper/desktop
    # Let's find rows with std > 5
    row_std = np.std(lum, axis=1)
    col_std = np.std(lum, axis=0)
    
    active_rows = np.where(row_std > 8)[0]
    active_cols = np.where(col_std > 8)[0]
    
    if len(active_rows) > 0 and len(active_cols) > 0:
        print(f"Active pill region: Y=[{active_rows[0]}..{active_rows[-1]}] (height={active_rows[-1]-active_rows[0]+1}px)")
        print(f"Active pill region: X=[{active_cols[0]}..{active_cols[-1]}] (width={active_cols[-1]-active_cols[0]+1}px)")
        
        # Crop exactly that region
        sub = im.crop((active_cols[0], active_rows[0], active_cols[-1], active_rows[-1]))
        sub.save(f'd:/Milind\Voice Assistant/.agents/teamwork/explorer_survey_ux/crops/highres/isolated_{name}')
        print(f"Saved isolated_{name}")
    else:
        print("No active region found.")

inspect_bar("wispr_flow_bottom_bar_44s.png")
inspect_bar("wispr_flow_bottom_bar_48s.png")
inspect_bar("wispr_flow_bottom_bar_50s.png")
inspect_bar("vox_bottom_bar_08s.png")
inspect_bar("vox_bottom_bar_36s.png")

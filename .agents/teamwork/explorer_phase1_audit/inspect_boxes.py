import av
from PIL import Image
import numpy as np

# Let's inspect the bounding boxes and text areas in frames_teams_example
img = Image.open(r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\frames_teams_example\frame_000173.jpg")
print("Teams Example Image 173 size:", img.size)

# Let's inspect frames_1031_020100/frame_004516.jpg
img2 = Image.open(r"D:\Milind\Voice Assistant\.agents\teamwork\explorer_phase1_audit\frames_1031_020100\frame_004516.jpg")
print("Meeting 1031 Image 4516 size:", img2.size)

# Let's check pixel variances to detect where content is located
arr2 = np.array(img2)
# row variance and col variance
row_var = np.var(arr2, axis=(1, 2))
col_var = np.var(arr2, axis=(0, 2))
print("Image 4516 active row range:", np.where(row_var > 10)[0][[0, -1]] if np.any(row_var > 10) else "None")
print("Image 4516 active col range:", np.where(col_var > 10)[0][[0, -1]] if np.any(col_var > 10) else "None")

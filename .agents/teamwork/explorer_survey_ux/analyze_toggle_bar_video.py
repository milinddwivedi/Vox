import av
import os
from PIL import Image

video_path = r'D:\Milind\Voice Assistant\External References\Toggle Bar Comparision.mp4'
output_dir = r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\frames'
os.makedirs(output_dir, exist_ok=True)

container = av.open(video_path)
video_stream = container.streams.video[0]

fps = float(video_stream.average_rate)
duration_sec = float(container.duration) / 1000000.0 if container.duration else 0
print(f"Video FPS: {fps}, Duration: {duration_sec:.2f}s, Dimensions: {video_stream.width}x{video_stream.height}")

# Sample frames every 1 second (or 0.5s if short)
step = int(fps) if duration_sec > 60 else max(1, int(fps / 2))
frame_count = 0
saved_count = 0

for frame in container.decode(video_stream):
    if frame_count % step == 0:
        sec = frame_count / fps
        img = frame.to_image()
        out_name = f"frame_{sec:06.2f}s.jpg"
        img.save(os.path.join(output_dir, out_name), quality=80)
        saved_count += 1
    frame_count += 1

print(f"Extraction complete! Saved {saved_count} frames across {frame_count} total frames.")

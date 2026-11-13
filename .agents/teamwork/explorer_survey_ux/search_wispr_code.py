import json
import struct
import re

asar_path = r'C:\Users\milin\AppData\Local\WisprFlow\app-1.6.1457\resources\app.asar'

with open(asar_path, 'rb') as f:
    header_size_data = f.read(16)
    size1, size2, size3, json_len = struct.unpack('<IIII', header_size_data)
    json_bytes = f.read(json_len)
    base_data_offset = size2 + 8
    header = json.loads(json_bytes.decode('utf-8'))
    
    def extract_file(rel_path):
        parts = rel_path.split('/')
        node = header
        for p in parts:
            node = node["files"][p]
        offset = int(node["offset"])
        size = int(node["size"])
        f.seek(base_data_offset + offset)
        return f.read(size)

    overlay_js = extract_file(".webpack/renderer/overlay/index.js").decode('utf-8', errors='ignore')
    status_js = extract_file(".webpack/renderer/status/index.js").decode('utf-8', errors='ignore')
    meeting_js = extract_file(".webpack/renderer/meeting_recorder/index.js").decode('utf-8', errors='ignore')

print("Overlay JS length:", len(overlay_js))
print("Status JS length:", len(status_js))
print("Meeting JS length:", len(meeting_js))

# Let's search for keywords in overlay_js, status_js, meeting_js
for name, content in [("Overlay", overlay_js), ("Status", status_js), ("Meeting", meeting_js)]:
    print(f"\n--- Keywords in {name} ---")
    for kw in ["mini", "toggle", "upcoming", "joining", "active", "disconnecting", "back-to-back", "permission", "recovery", "notetaker"]:
        matches = len(re.findall(kw, content, re.IGNORECASE))
        print(f"  {kw}: {matches} matches")

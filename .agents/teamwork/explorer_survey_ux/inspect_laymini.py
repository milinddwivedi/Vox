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

    status_js = extract_file(".webpack/renderer/status/index.js").decode('utf-8', errors='ignore')

# Find layMini
idx = status_js.find("layMini")
if idx != -1:
    print("Found layMini at", idx)
    print("Code surrounding layMini (1500 chars):")
    print(status_js[max(0, idx-800):min(len(status_js), idx+1200)])
else:
    print("layMini not found")

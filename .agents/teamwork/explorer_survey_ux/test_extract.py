import json
import struct

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

    html = extract_file(".webpack/renderer/overlay/index.html")
    print("Overlay index.html:\n", html.decode('utf-8'))

    status_html = extract_file(".webpack/renderer/status/index.html")
    print("Status index.html:\n", status_html.decode('utf-8'))

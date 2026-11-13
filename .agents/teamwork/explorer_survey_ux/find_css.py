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
    
    files = []
    def traverse(node, path=""):
        if "files" in node:
            for name, child in node["files"].items():
                traverse(child, f"{path}/{name}" if path else name)
        else:
            files.append((path, node.get("size", 0), int(node.get("offset", 0))))
            
    traverse(header)
    
    css_files = [f for f in files if f[0].endswith('.css') or 'status' in f[0] or 'overlay' in f[0]]
    print(f"Checking {len(css_files)} files for layMini...")
    for f in css_files:
        fpath = f[0]
        offset = f[2]
        size = f[1]
        if size > 5000000: continue
        f.seek if hasattr(f, 'seek') else None
        
with open(asar_path, 'rb') as f:
    f.seek(base_data_offset)
    # Search for "layMini:" or "miniTitle:" in status_js
    status_node = header["files"][".webpack"]["files"]["renderer"]["files"]["status"]["files"]["index.js"]
    f.seek(base_data_offset + int(status_node["offset"]))
    data = f.read(int(status_node["size"])).decode('utf-8', errors='ignore')
    
    # Look for the CSS module object definition containing layMini
    m = re.search(r'layMini:\s*"([^"]+)"', data)
    if m:
        print("layMini class name:", m.group(1))
        # Find where the class name or layMini is defined with CSS
        cls_name = m.group(1)
        # Search for this class name in data
        for cm in re.finditer(re.escape(cls_name), data):
            idx = cm.start()
            print(f"CSS around {cls_name}:")
            print(data[max(0, idx-200):min(len(data), idx+300)])
    else:
        print("layMini map not found directly, searching for styles")

import json
import struct

asar_path = r'C:\Users\milin\AppData\Local\WisprFlow\app-1.6.1457\resources\app.asar'

with open(asar_path, 'rb') as f:
    header_size_data = f.read(16)
    size1, size2, size3, json_len = struct.unpack('<IIII', header_size_data)
    json_bytes = f.read(json_len)
    header = json.loads(json_bytes.decode('utf-8'))
    
    files = []
    def traverse(node, path=""):
        if "files" in node:
            for name, child in node["files"].items():
                traverse(child, f"{path}/{name}" if path else name)
        else:
            files.append((path, node.get("size", 0), node.get("offset", "0")))
            
    traverse(header)
    
    renderers = set()
    for f in files:
        if f[0].startswith(".webpack/renderer/"):
            parts = f[0].split('/')
            if len(parts) > 2:
                renderers.add(parts[2])
    print("Renderer subdirectories in Wispr Flow:")
    for r in sorted(renderers):
        print(" -", r)

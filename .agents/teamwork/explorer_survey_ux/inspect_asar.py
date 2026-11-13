import json
import struct

asar_path = r'C:\Users\milin\AppData\Local\WisprFlow\app-1.6.1457\resources\app.asar'

with open(asar_path, 'rb') as f:
    # ASAR format:
    # 4 bytes: size of pickle header
    # 4 bytes: size of pickle
    # 4 bytes: size of header pickle
    # 4 bytes: size of json header
    # JSON header string
    header_size_data = f.read(16)
    size1, size2, size3, json_len = struct.unpack('<IIII', header_size_data)
    print(f"Header sizes: {size1}, {size2}, {size3}, json_len={json_len}")
    
    # Read json header
    json_bytes = f.read(json_len)
    header = json.loads(json_bytes.decode('utf-8'))
    
    # Let's list files in the header
    files = []
    def traverse(node, path=""):
        if "files" in node:
            for name, child in node["files"].items():
                traverse(child, f"{path}/{name}" if path else name)
        else:
            files.append((path, node.get("size", 0), node.get("offset", "0")))
            
    traverse(header)
    print(f"Total files in asar: {len(files)}")
    
    # Find files matching toggle, widget, bar, pill, floating, meeting, notetaker, etc.
    matches = [f for f in files if any(k in f[0].lower() for k in ['toggle', 'widget', 'bar', 'pill', 'floating', 'meeting', 'notetaker', 'mini'])]
    print(f"\nFound {len(matches)} matching files:")
    for m in matches[:50]:
        print(f" - {m[0]} (size: {m[1]})")

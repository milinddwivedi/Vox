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
    
    status_node = header["files"][".webpack"]["files"]["renderer"]["files"]["status"]["files"]["index.js"]
    f.seek(base_data_offset + int(status_node["offset"]))
    data = f.read(int(status_node["size"])).decode('utf-8', errors='ignore')

idx = data.find('r.locals={pillRoot:"dKIPQWWGoQsUI497N8GY"')
end = data.find('}', idx)
locals_str = data[idx:end+1]
print("r.locals definition:")
print(locals_str)

# Parse mapping
mapping = {}
for m in re.finditer(r'([A-Za-z0-9_$]+):"([^"]+)"', locals_str):
    name = m.group(1)
    cls = m.group(2)
    mapping[cls] = name

print(f"\nParsed {len(mapping)} mappings:")
for c, n in mapping.items():
    print(f"  {n} = .{c}")

# Find full CSS
css_start = data.rfind('e.exports="', 0, idx)
if css_start == -1:
    css_start = data.rfind(',"', 0, idx)
# Find where the CSS string starts
# In webpack: e.push([t.id,"...CSS...",""])
push_idx = data.rfind('.push([', 0, idx)
if push_idx != -1:
    css_chunk = data[push_idx:idx]
    # replace
    for c, n in sorted(mapping.items(), key=lambda x: -len(x[0])):
        css_chunk = css_chunk.replace(c, f"__CLASS_{n}__")
    with open(r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\wispr_pill_semantic_css.txt', 'w', encoding='utf-8') as sf:
        sf.write(css_chunk)
    print("Saved semantic CSS!")

import json
import struct

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
if idx != -1:
    print("500 chars before r.locals:")
    print(data[max(0, idx-500):idx])
    # Search backwards for e.push or string assignment
    bstart = max(0, idx - 8000)
    chunk = data[bstart:idx]
    # find where string starts
    quote_idx = chunk.rfind('`')
    if quote_idx == -1:
        quote_idx = chunk.rfind('",')
    print("Chunk from quote_idx:")
    print(chunk[max(0, len(chunk)-1500):])
    with open(r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\wispr_pill_css.txt', 'w', encoding='utf-8') as cf:
        cf.write(chunk)

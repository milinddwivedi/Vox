import glob
import json
import os
import av
import numpy as np

diag_files = sorted(glob.glob(r'D:\Milind\Voice Assistant\data\recordings\meeting_*\capture_diagnostics.json'))
print(f"Found {len(diag_files)} capture_diagnostics.json files:")
for df in diag_files:
    m_name = os.path.basename(os.path.dirname(df))
    try:
        with open(df, 'r', encoding='utf-8') as f:
            d = json.load(f)
            vh = d.get('video_health', {})
            plat = d.get('platform_name')
            hwnd = d.get('locked_hwnd')
            title = d.get('locked_window_title')
            enc = d.get('frames_encoded')
            val = vh.get('valid_frames')
            pres = vh.get('presentation_frames')
            call = vh.get('call_stage_frames')
            priv = vh.get('privacy_pause_frames')
            health = vh.get('health_status')
            res = d.get('resolution')
            print(f"{m_name} | Plat: {plat} | HWND: {hwnd} | Enc: {enc} | Valid: {val} | Pres: {pres} | Stage: {call} | Pause: {priv} | Res: {res} | Health: {health} | Title: {title}")
    except Exception as e:
        print(f"{m_name} | ERROR: {e}")

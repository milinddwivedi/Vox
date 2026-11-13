import ctypes
from ctypes import wintypes
import numpy as np

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

windows = []
def enum_cb(hwnd, lparam):
    if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w > 200 and h > 200:
                windows.append((hwnd, buf.value, w, h))
    return True

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

print(f"Found {len(windows)} visible windows:")
for h, title, w, h_dim in windows[:10]:
    print(f"  HWND={h}: '{title}' ({w}x{h_dim})")

if windows:
    test_hwnd, test_title, w, h_dim = windows[0]
    hdc_win = user32.GetWindowDC(test_hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h_dim)
    old = gdi32.SelectObject(hdc_mem, hbmp)
    ok = user32.PrintWindow(test_hwnd, hdc_mem, 2)
    print(f"\nTesting PrintWindow on HWND={test_hwnd} ('{test_title}'):")
    print(f"  PrintWindow PW_RENDERFULLCONTENT returned: {ok}")
    
    bmi = ctypes.create_string_buffer(40)
    ctypes.c_uint32.from_buffer(bmi, 0).value = 40
    ctypes.c_int32.from_buffer(bmi, 4).value = w
    ctypes.c_int32.from_buffer(bmi, 8).value = -h_dim
    ctypes.c_uint16.from_buffer(bmi, 12).value = 1
    ctypes.c_uint16.from_buffer(bmi, 14).value = 32
    ctypes.c_uint32.from_buffer(bmi, 16).value = 0

    buf = np.empty((h_dim, w, 4), dtype=np.uint8)
    lines = gdi32.GetDIBits(hdc_mem, hbmp, 0, h_dim, ctypes.c_void_p(buf.ctypes.data), ctypes.byref(bmi), 0)
    print(f"  GetDIBits lines: {lines}, mean pixel value: {np.mean(buf):.2f}, max pixel: {np.max(buf)}")
    
    gdi32.SelectObject(hdc_mem, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(test_hwnd, hdc_win)

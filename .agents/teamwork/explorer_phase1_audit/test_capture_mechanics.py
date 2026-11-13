import ctypes
from ctypes import wintypes
import numpy as np

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

hwnd = user32.GetForegroundWindow()
length = user32.GetWindowTextLengthW(hwnd)
buf = ctypes.create_unicode_buffer(length + 1)
user32.GetWindowTextW(hwnd, buf, length + 1)
print(f"Foreground HWND={hwnd}, Title='{buf.value}'")

# Test PrintWindow on foreground window
hdc_win = user32.GetWindowDC(hwnd)
rect = wintypes.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(rect))
w = rect.right - rect.left
h = rect.bottom - rect.top
print(f"Window bounds: {w}x{h}")

hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
hbmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
old = gdi32.SelectObject(hdc_mem, hbmp)
ok = user32.PrintWindow(hwnd, hdc_mem, 2) # PW_RENDERFULLCONTENT
print(f"PrintWindow PW_RENDERFULLCONTENT returned: {ok}")

# Read pixels
bmi = ctypes.create_string_buffer(40)
ctypes.c_uint32.from_buffer(bmi, 0).value = 40
ctypes.c_int32.from_buffer(bmi, 4).value = w
ctypes.c_int32.from_buffer(bmi, 8).value = -h
ctypes.c_uint16.from_buffer(bmi, 12).value = 1
ctypes.c_uint16.from_buffer(bmi, 14).value = 32
ctypes.c_uint32.from_buffer(bmi, 16).value = 0

buf = np.empty((h, w, 4), dtype=np.uint8)
lines = gdi32.GetDIBits(hdc_mem, hbmp, 0, h, ctypes.c_void_p(buf.ctypes.data), ctypes.byref(bmi), 0)
print(f"GetDIBits lines: {lines}, mean pixel value: {np.mean(buf):.2f}, max pixel: {np.max(buf)}")

gdi32.SelectObject(hdc_mem, old)
gdi32.DeleteObject(hbmp)
gdi32.DeleteDC(hdc_mem)
user32.ReleaseDC(hwnd, hdc_win)

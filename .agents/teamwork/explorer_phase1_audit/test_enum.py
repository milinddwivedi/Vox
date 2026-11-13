from voxlocal.video.meeting_capture import enum_desktop_windows, get_window_title, get_window_rect, user32

windows = []
def cb(hwnd, lparam):
    if user32.IsWindowVisible(hwnd):
        title = get_window_title(hwnd)
        rect = get_window_rect(hwnd)
        if rect[2] > 100 and rect[3] > 100:
            windows.append((hwnd, title, rect))
    return True

enum_desktop_windows(cb)
print(f"enum_desktop_windows found {len(windows)} windows:")
for h, t, r in windows[:15]:
    print(f"  HWND={h}: '{t}' ({r[2]}x{r[3]})")

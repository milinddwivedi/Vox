"""
VOX Voice Assistant — Lightweight Native Windows Installer (VoxLocal-Setup.exe).
Zero-dependency Win32 + stdlib installer that installs VOX into %LOCALAPPDATA%\\Programs\\VOX,
creates Desktop & Start Menu shortcuts, registers Windows startup, and launches VoxLocal.exe.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

RELEASE_ZIP_URL = "https://github.com/milinddwivedi/Vox/releases/latest/download/VoxLocal-Portable-win64.zip"
APP_NAME = "VOX Voice Assistant"
EXE_NAME = "VoxLocal.exe"

MB_OK = 0x00000000
MB_OKCANCEL = 0x00000001
MB_YESNO = 0x00000004
MB_ICONINFORMATION = 0x00000040
MB_ICONERROR = 0x00000010
IDOK = 1
IDYES = 6


def msgbox(title: str, text: str, flags: int = MB_OK | MB_ICONINFORMATION) -> int:
    try:
        return int(ctypes.windll.user32.MessageBoxW(0, text, title, flags))
    except Exception:
        return IDOK


def get_install_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / "Programs" / "VOX"


def create_shortcut(target_exe: Path, shortcut_path: Path, description: str) -> None:
    ps_cmd = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{shortcut_path}'); "
        f"$s.TargetPath = '{target_exe}'; "
        f"$s.WorkingDirectory = '{target_exe.parent}'; "
        f"$s.Description = '{description}'; "
        f"$s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )


def register_startup(target_exe: Path, enable: bool = True) -> None:
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(
                    key,
                    "VoxLocalVoiceAssistant",
                    0,
                    winreg.REG_SZ,
                    f'"{target_exe}" --startup --minimized',
                )
    except Exception:
        pass


def run_install(silent: bool = False) -> int:
    install_dir = get_install_dir()
    if not silent:
        resp = msgbox(
            "VOX Voice Assistant — Windows Setup",
            f"Install VOX Voice Assistant to:\n{install_dir}\n\n"
            "• Creates Desktop & Start Menu shortcuts\n"
            "• Configures Windows login startup\n"
            "• Launches VOX automatically when ready\n\n"
            "Click OK to begin installation.",
            MB_OKCANCEL | MB_ICONINFORMATION,
        )
        if resp != IDOK:
            return 0

    try:
        print("[1/4] Stopping any running VOX instance...")
        subprocess.run(
            ["taskkill", "/F", "/IM", EXE_NAME],
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )

        exe_dir = Path(sys.executable).resolve().parent
        local_zip = exe_dir / "VoxLocal-Portable-win64.zip"
        tmp_zip = None

        if local_zip.exists():
            print(f"[2/4] Found local package: {local_zip}")
            zip_path = local_zip
        else:
            print(f"[2/4] Downloading VOX package from GitHub Releases...")
            fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="vox_setup_")
            os.close(fd)
            tmp_zip = Path(tmp_name)
            zip_path = tmp_zip

            req = urllib.request.Request(
                RELEASE_ZIP_URL,
                headers={"User-Agent": "VOX-Windows-Installer/1.0"},
            )
            with urllib.request.urlopen(req, timeout=180) as resp_http, open(zip_path, "wb") as out_f:
                total = int(resp_http.headers.get("Content-Length", "0") or "0")
                downloaded = 0
                chunk_sz = 256 * 1024
                last_pct = -1
                while True:
                    chunk = resp_http.read(chunk_sz)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = int((downloaded * 100) / total)
                        if pct >= last_pct + 10:
                            last_pct = pct
                            print(
                                f"      Downloaded {downloaded / (1024*1024):.1f} / {total / (1024*1024):.1f} MB ({pct}%)"
                            )

        print(f"[3/4] Extracting VOX to {install_dir}...")
        install_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(install_dir)

        if tmp_zip and tmp_zip.exists():
            try:
                tmp_zip.unlink()
            except Exception:
                pass

        target_exe = install_dir / EXE_NAME
        if not target_exe.exists():
            nested = install_dir / "VoxLocal" / EXE_NAME
            if nested.exists():
                target_exe = nested

        print("[4/4] Creating Desktop & Start Menu shortcuts...")
        start_menu_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if start_menu_dir.exists():
            create_shortcut(target_exe, start_menu_dir / f"{APP_NAME}.lnk", APP_NAME)

        desktop_dir = Path.home() / "Desktop"
        if desktop_dir.exists():
            create_shortcut(target_exe, desktop_dir / f"{APP_NAME}.lnk", APP_NAME)

        register_startup(target_exe, True)

        if target_exe.exists():
            print("Launching VOX Voice Assistant...")
            subprocess.Popen([str(target_exe)], cwd=str(target_exe.parent))

        if not silent:
            msgbox(
                "VOX Installed Successfully",
                f"VOX Voice Assistant has been installed to:\n{install_dir}\n\n"
                "Desktop & Start Menu shortcuts have been created and VOX is now launching!",
                MB_OK | MB_ICONINFORMATION,
            )
        return 0
    except Exception as exc:
        if not silent:
            msgbox("VOX Setup Error", f"Installation failed:\n{exc}", MB_OK | MB_ICONERROR)
        return 1


def main() -> int:
    if "--smoke-test" in sys.argv:
        p = get_install_dir()
        print(f"SMOKE_OK install_dir={p}")
        return 0
    return run_install(silent=("--silent" in sys.argv))


if __name__ == "__main__":
    sys.exit(main())

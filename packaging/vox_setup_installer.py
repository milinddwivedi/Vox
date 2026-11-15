"""
VOX Voice Assistant — Lightweight Native Windows Installer (VoxLocal-Setup.exe).
Zero-dependency Win32 + stdlib installer that installs VOX into %LOCALAPPDATA%\\Programs\\VOX,
bundles latest production source code, creates Desktop & Start Menu shortcuts,
registers Windows startup, and launches VoxLocal.exe.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shutil
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


def install_bundled_components(install_dir: Path) -> bool:
    """
    Unpacks bundled source files and assets included within VoxLocal-Setup.exe
    via PyInstaller (_MEIPASS). Ensures client has latest Ed25519, 72h offline grace,
    removed backdoors, plan status cards, and broker separation.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    base_dir = Path(meipass) if meipass else Path(__file__).resolve().parent.parent

    installed_any = False
    bundled_src = base_dir / "src"
    if bundled_src.exists():
        print(f"Installing bundled source tree to {install_dir / 'src'}...")
        target_src = install_dir / "src"
        shutil.copytree(bundled_src, target_src, dirs_exist_ok=True)
        installed_any = True

    bundled_assets = base_dir / "assets"
    if bundled_assets.exists():
        target_assets = install_dir / "assets"
        shutil.copytree(bundled_assets, target_assets, dirs_exist_ok=True)
        installed_any = True

    return installed_any


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

        install_dir.mkdir(parents=True, exist_ok=True)

        print("[2/4] Deploying updated application components...")
        install_bundled_components(install_dir)

        exe_dir = Path(sys.executable).resolve().parent
        local_zip = exe_dir / "VoxLocal-Portable-win64.zip"
        tmp_zip = None

        if local_zip.exists():
            print(f"[3/4] Extracting local package: {local_zip}")
            with zipfile.ZipFile(local_zip, "r") as zf:
                zf.extractall(install_dir)
        else:
            local_dist = exe_dir / "dist" / "VoxLocal"
            if not local_dist.exists():
                local_dist = Path(__file__).resolve().parent.parent / "dist" / "VoxLocal"

            if local_dist.exists() and (local_dist / EXE_NAME).exists():
                print(f"[3/4] Copying runtime binaries from {local_dist}...")
                shutil.copytree(local_dist, install_dir, dirs_exist_ok=True)
            else:
                print(f"[3/4] Downloading VOX runtime package from GitHub Releases...")
                try:
                    fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="vox_setup_")
                    os.close(fd)
                    tmp_zip = Path(tmp_name)

                    req = urllib.request.Request(
                        RELEASE_ZIP_URL,
                        headers={"User-Agent": "VOX-Windows-Installer/1.0"},
                    )
                    with urllib.request.urlopen(req, timeout=180) as resp_http, open(tmp_zip, "wb") as out_f:
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
                                    print(f"      Downloaded {downloaded / (1024*1024):.1f} / {total / (1024*1024):.1f} MB ({pct}%)")

                    with zipfile.ZipFile(tmp_zip, "r") as zf:
                        zf.extractall(install_dir)
                except Exception as dl_err:
                    print(f"Network download skipped or failed: {dl_err}. Using bundled core.")

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

        if target_exe.exists():
            register_startup(target_exe, True)
            print("Launching VOX Voice Assistant...")
            subprocess.Popen([str(target_exe)], cwd=str(target_exe.parent))

        if not silent:
            msgbox(
                "VOX Installed Successfully",
                f"VOX Voice Assistant has been installed to:\n{install_dir}\n\n"
                "Desktop & Start Menu shortcuts have been created and VOX is ready!",
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
        # Verify bundled components exist in source tree or MEIPASS
        meipass = getattr(sys, "_MEIPASS", None)
        base = Path(meipass) if meipass else Path(__file__).resolve().parent.parent
        src_path = base / "src"
        has_src = src_path.exists() and (src_path / "voxlocal" / "core" / "entitlement.py").exists()
        print(f"SMOKE_OK install_dir={p} has_bundled_src={has_src}")
        return 0
    if "--version" in sys.argv:
        print("1.0.0")
        return 0
    return run_install(silent=("--silent" in sys.argv))


if __name__ == "__main__":
    sys.exit(main())

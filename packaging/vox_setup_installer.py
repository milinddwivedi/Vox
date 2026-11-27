"""
VOX Voice Assistant — Resilient Native Windows Installer, Wizard, Updater & Uninstaller (VoxLocal-Setup.exe).

Supports:
- Visible modern PyQt6 Setup Wizard when launched interactively (`VoxLocal-Setup.exe`)
- Clean installations into `%LOCALAPPDATA%\\Programs\\VOX` (or `--install-dir`), keeping user data separate at `%LOCALAPPDATA%\\VOX`
- Full extraction of compressed `bundled_payload/app_payload.tar.xz` OR incremental bundled_bin/src/assets/_internal deltas
- Automatic SQLite `.backup()` before upgrades + never touching user recordings/databases
- Online model provisioning & SHA-256 verification via `ModelProvisioner` (reusing already-verified models)
- Graceful `--wait-pid` teardown, pre-launch `--verify-runtime`, automatic rollback on failure
- Registered Windows Add/Remove Programs uninstaller (`--uninstall`) preserving user data by default
"""

from __future__ import annotations

import ctypes
from datetime import datetime
import hashlib
import json
import lzma
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.request
import zipfile

APP_NAME = "VOX Voice Assistant"
EXE_NAME = "VoxLocal.exe"
UNINSTALL_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\VOXVoiceAssistant"

MB_OK = 0x00000000
MB_OKCANCEL = 0x00000001
MB_ICONINFORMATION = 0x00000040
MB_ICONERROR = 0x00000010
IDOK = 1

_LOG_PATHS: List[Path] = []

InstallerProgressCb = Callable[[int, str, str, int, int], None]


def _ensure_import_paths(base_dir: Optional[Path] = None) -> None:
    candidates = []
    if base_dir is not None:
        candidates.extend([base_dir, base_dir / "src", base_dir / "packaging"])
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp = Path(meipass)
        candidates.extend([mp, mp / "src", mp / "packaging"])
    repo_root = Path(__file__).resolve().parent.parent
    candidates.extend([repo_root, repo_root / "src", repo_root / "packaging"])
    for cand in candidates:
        s = str(cand)
        if cand.exists() and s not in sys.path:
            sys.path.insert(0, s)


def _init_installer_logs(install_dir: Optional[Path] = None) -> None:
    candidates: List[Path] = [Path(tempfile.gettempdir()) / "vox_updater" / "vox_installer.log"]
    if install_dir is not None:
        data_logs = _resolve_install_data_dir(Path(install_dir)) / "logs" / "vox_installer.log"
        candidates.append(data_logs)
    for p in candidates:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p not in _LOG_PATHS:
                _LOG_PATHS.append(p)
        except Exception:
            pass


def log_line(msg: str, log_cb: Optional[Callable[[str], None]] = None) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    if log_cb is not None:
        try:
            log_cb(line)
        except Exception:
            pass
    for p in list(_LOG_PATHS):
        try:
            with p.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass


def msgbox(title: str, text: str, flags: int = MB_OK | MB_ICONINFORMATION) -> int:
    try:
        return int(ctypes.windll.user32.MessageBoxW(0, text, title, flags))
    except Exception:
        return IDOK


def get_install_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / "Programs" / "VOX"


def _resolve_install_data_dir(install_dir: Path, install_dir_override: Optional[Path] = None) -> Path:
    env_data = os.environ.get("VOXLOCAL_DATA_DIR", "").strip()
    if env_data:
        return Path(env_data).resolve()
    if install_dir_override is not None and install_dir.resolve() != get_install_dir().resolve():
        # Isolated test or custom directory: keep test artifacts inside the custom target dir
        return (install_dir / "data").resolve()
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return (Path(local_app_data).resolve() / "VOX" / "data").resolve()


def _detect_system_ram_gb() -> float:
    try:
        from ctypes import wintypes

        class _MEM(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        st = _MEM()
        st.dwLength = ctypes.sizeof(_MEM)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return round(st.ullTotalPhys / (1024**3), 1)
    except Exception:
        pass
    return 16.0


def _get_windows_desktop_dir() -> Path:
    """Resolve user's active Desktop folder via Windows Known Folder API (handles OneDrive KFM)."""
    try:
        import uuid
        from ctypes import wintypes

        folder_id = uuid.UUID("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}")

        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        g = _GUID(
            folder_id.time_low,
            folder_id.time_mid,
            folder_id.time_hi_version,
            (ctypes.c_ubyte * 8).from_buffer_copy(folder_id.bytes[8:]),
        )
        p_path = ctypes.c_wchar_p()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p_path))
        if hr == 0 and p_path.value:
            resolved = Path(p_path.value)
            ctypes.windll.ole32.CoTaskMemFree(p_path)
            if resolved.exists():
                return resolved
    except Exception:
        pass
    onedrive_desktop = Path.home() / "OneDrive" / "Desktop"
    if onedrive_desktop.is_dir():
        return onedrive_desktop
    return Path.home() / "Desktop"


def _resolve_install_models_dir(install_dir: Path, data_dir: Path) -> Path:
    env_m = os.environ.get("VOXLOCAL_MODELS_DIR", "").strip()
    if env_m:
        return Path(env_m).resolve()
    if data_dir == (install_dir / "data"):
        return data_dir / "models"
    return data_dir.parent / "models"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def create_shortcut(target_exe: Path, shortcut_path: Path, description: str, arguments: str = "") -> None:
    arg_line = f"$s.Arguments = '{arguments}'; " if arguments else ""
    ps_cmd = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{shortcut_path}'); "
        f"$s.TargetPath = '{target_exe}'; "
        f"{arg_line}"
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
            else:
                try:
                    winreg.DeleteValue(key, "VoxLocalVoiceAssistant")
                except FileNotFoundError:
                    pass
    except Exception:
        pass


def register_windows_uninstaller(install_dir: Path, uninstaller_exe: Path, version: str) -> None:
    import winreg

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REG_KEY) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version or "2.1.0")
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "VOX Local AI")
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(install_dir / EXE_NAME))
            winreg.SetValueEx(
                key,
                "UninstallString",
                0,
                winreg.REG_SZ,
                f'"{uninstaller_exe}" --uninstall --install-dir "{install_dir}"',
            )
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
    except Exception:
        pass


def unregister_windows_uninstaller() -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REG_KEY)
    except Exception:
        pass


def parse_cli_args(argv: List[str]) -> Dict[str, Any]:
    """Parse installer arguments, safely recovering unquoted space-split --install-dir paths."""
    silent = False
    no_launch = False
    relaunch = False
    skip_models = False
    uninstall = False
    purge_data = False
    register_uninstall = False
    wait_pid = 0
    expected_version = ""
    from_version = ""
    install_dir_override: Optional[Path] = None
    gui_screenshot_dir: Optional[Path] = None

    known_flags = {
        "--silent", "-s", "/s", "/silent", "/verysilent", "/suppressmsgboxes", "/norestart", "/sp-",
        "--no-launch", "--relaunch", "--install-dir", "--wait-pid", "--expected-version", "--from-version",
        "--smoke-test", "--version", "--skip-models", "--uninstall", "--purge-data",
        "--register-uninstall", "--gui-screenshot",
    }

    i = 0
    while i < len(argv):
        arg = argv[i]
        low = arg.lower()
        if low in ("--silent", "-s", "/s", "/silent", "/verysilent", "/suppressmsgboxes", "/norestart", "/sp-"):
            silent = True
        elif low == "--no-launch":
            no_launch = True
        elif low == "--relaunch":
            relaunch = True
            no_launch = False
        elif low == "--skip-models":
            skip_models = True
        elif low == "--uninstall":
            uninstall = True
        elif low == "--purge-data":
            purge_data = True
        elif low == "--register-uninstall":
            register_uninstall = True
        elif low.startswith("--gui-screenshot="):
            gui_screenshot_dir = Path(arg.split("=", 1)[1])
        elif low == "--gui-screenshot" and i + 1 < len(argv):
            gui_screenshot_dir = Path(argv[i + 1])
            i += 1
        elif low.startswith("--wait-pid="):
            try:
                wait_pid = int(arg.split("=", 1)[1].strip())
            except ValueError:
                pass
        elif low == "--wait-pid" and i + 1 < len(argv):
            try:
                wait_pid = int(argv[i + 1].strip())
            except ValueError:
                pass
            i += 1
        elif low.startswith("--expected-version="):
            expected_version = arg.split("=", 1)[1].strip()
        elif low == "--expected-version" and i + 1 < len(argv):
            expected_version = argv[i + 1].strip()
            i += 1
        elif low.startswith("--from-version="):
            from_version = arg.split("=", 1)[1].strip()
        elif low == "--from-version" and i + 1 < len(argv):
            from_version = argv[i + 1].strip()
            i += 1
        elif low.startswith("--install-dir="):
            install_dir_override = Path(arg.split("=", 1)[1])
        elif low == "--install-dir" and i + 1 < len(argv):
            parts: List[str] = []
            j = i + 1
            while j < len(argv):
                nxt = argv[j]
                if nxt.lower().split("=", 1)[0] in known_flags or nxt.startswith("--") or nxt.startswith("/"):
                    break
                parts.append(nxt)
                j += 1
            if parts:
                install_dir_override = Path(" ".join(parts))
            i = j - 1
        i += 1

    return {
        "silent": silent,
        "no_launch": no_launch and not relaunch,
        "relaunch": relaunch,
        "skip_models": skip_models,
        "uninstall": uninstall,
        "purge_data": purge_data,
        "register_uninstall": register_uninstall,
        "wait_pid": wait_pid,
        "expected_version": expected_version,
        "from_version": from_version,
        "install_dir_override": install_dir_override,
        "gui_screenshot_dir": gui_screenshot_dir,
    }


def _wait_for_pid_exit(pid: int, timeout_sec: float = 15.0) -> bool:
    """Wait gracefully for a running parent VoxLocal PID to terminate before replacing binaries."""
    if pid <= 0:
        return True
    SYNCHRONIZE = 0x00100000
    WAIT_OBJECT_0 = 0x00000000
    kernel32 = getattr(getattr(ctypes, "windll", None), "kernel32", None)
    if kernel32 is not None:
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if handle:
            try:
                res = kernel32.WaitForSingleObject(handle, int(timeout_sec * 1000))
                if res == WAIT_OBJECT_0:
                    return True
            finally:
                kernel32.CloseHandle(handle)
        else:
            return True

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
            if str(int(pid)) not in (r.stdout or ""):
                return True
        except Exception:
            break
        time.sleep(0.25)
    return False


def _stop_target_vox_processes(install_dir: Path, wait_pid: int = 0, force_all_if_default: bool = True) -> None:
    if wait_pid > 0:
        log_line(f"Waiting up to 15s for parent PID {wait_pid} to exit gracefully...")
        exited = _wait_for_pid_exit(wait_pid, timeout_sec=15.0)
        if not exited:
            log_line(f"Parent PID {wait_pid} still active after 15s; forcing shutdown...")
            subprocess.run(
                ["taskkill", "/F", "/PID", str(int(wait_pid))],
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
            time.sleep(0.6)

    norm_dir = str(install_dir.resolve()).lower()
    ps_kill = (
        "Get-CimInstance Win32_Process -Filter \"Name='VoxLocal.exe'\" | "
        "Where-Object { $_.ExecutablePath -and $_.ExecutablePath.ToLower().StartsWith('"
        + norm_dir.replace("'", "''")
        + "') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_kill],
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )

    if force_all_if_default:
        subprocess.run(
            ["taskkill", "/F", "/IM", EXE_NAME],
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
        subprocess.run(
            ["taskkill", "/F", "/IM", "llama-server.exe"],
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )

    existing_exe = install_dir / EXE_NAME
    for _ in range(30):
        if not existing_exe.exists():
            break
        try:
            with existing_exe.open("ab"):
                pass
            break
        except OSError:
            time.sleep(0.3)


def _backup_database_if_exists(data_dir: Path) -> Optional[Path]:
    db_file = data_dir / "voxlocal.db"
    if not db_file.is_file():
        return None
    backups_dir = data_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = backups_dir / f"voxlocal_pre_upgrade_{ts}.db"
    try:
        sconn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        dconn = sqlite3.connect(str(dst))
        sconn.backup(dconn)
        sconn.close()
        dconn.close()
        return dst
    except Exception:
        return None


def _copy_file_with_backup(
    src_file: Path,
    dst_file: Path,
    install_dir: Path,
    backup_dir: Optional[Path],
    created_rel: List[str],
    overwritten_rel: List[str],
) -> None:
    rel = str(dst_file.relative_to(install_dir)).replace("\\", "/")
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    if dst_file.exists():
        if backup_dir is not None:
            bk = backup_dir / rel
            bk.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst_file, bk)
        if rel not in overwritten_rel:
            overwritten_rel.append(rel)
    else:
        if rel not in created_rel:
            created_rel.append(rel)
    shutil.copy2(src_file, dst_file)


def _copy_tree_with_backup(
    src_tree: Path,
    dst_tree: Path,
    install_dir: Path,
    backup_dir: Optional[Path],
    created_rel: List[str],
    overwritten_rel: List[str],
) -> int:
    count = 0
    for item in src_tree.rglob("*"):
        if item.is_file():
            rel_sub = item.relative_to(src_tree)
            _copy_file_with_backup(
                item,
                dst_tree / rel_sub,
                install_dir,
                backup_dir,
                created_rel,
                overwritten_rel,
            )
            count += 1
    return count


def _restore_from_backup(install_dir: Path, backup_dir: Path, created_rel: List[str]) -> bool:
    ok = True
    log_line(f"Restoring previous installation from {backup_dir}...")
    for rel in created_rel:
        p = install_dir / rel
        if p.exists():
            try:
                p.unlink()
            except Exception as exc:
                log_line(f"Warning deleting newly created {p}: {exc}")
    if backup_dir.exists():
        for bk in backup_dir.rglob("*"):
            if bk.is_file() and bk.name != "_backup_meta.json":
                rel = bk.relative_to(backup_dir)
                dst = install_dir / rel
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(bk, dst)
                except Exception as exc:
                    log_line(f"Error restoring {dst}: {exc}")
                    ok = False
    return ok


def _extract_tar_payload_with_backup(
    tar_path: Path,
    install_dir: Path,
    backup_dir: Optional[Path],
    created_rel: List[str],
    overwritten_rel: List[str],
    *,
    cancel_event: Optional[threading.Event] = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> int:
    """Extract compressed app_payload.tar.xz cleanly while backing up overwritten files and never touching data/."""
    count = 0
    with tarfile.open(tar_path, mode="r:xz") as tf:
        members = [m for m in tf.getmembers() if m.isfile()]
        total = max(1, len(members))
        for idx, member in enumerate(members, start=1):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Cancelled during application extraction")
            norm_rel = Path(member.name.replace("\\", "/").lstrip("/"))
            if ".." in norm_rel.parts:
                continue
            rel_str = str(norm_rel).replace("\\", "/")
            if rel_str == "data" or rel_str.startswith("data/"):
                continue
            dst_file = (install_dir / norm_rel).resolve()
            dst_file.relative_to(install_dir.resolve())  # path traversal guard
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            if dst_file.exists():
                if backup_dir is not None:
                    bk = backup_dir / norm_rel
                    bk.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dst_file, bk)
                if rel_str not in overwritten_rel:
                    overwritten_rel.append(rel_str)
            else:
                if rel_str not in created_rel:
                    created_rel.append(rel_str)
            src_stream = tf.extractfile(member)
            if src_stream is not None:
                tmp_dst = dst_file.with_name(dst_file.name + ".stage_tmp")
                with src_stream, tmp_dst.open("wb") as out_f:
                    shutil.copyfileobj(src_stream, out_f, length=256 * 1024)
                os.replace(tmp_dst, dst_file)
                count += 1
            if progress_cb is not None and (idx % 25 == 0 or idx == total):
                progress_cb(idx, total, norm_rel.name)
    return count


def install_bundled_components(
    install_dir: Path,
    backup_dir: Optional[Path] = None,
    *,
    cancel_event: Optional[threading.Event] = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> Dict[str, Any]:
    """
    Atomically deploys bundled payload (`app_payload.tar.xz` or legacy `bundled_bin`/`src`/`assets`/`_internal`)
    while backing up replaced files and never touching `install_dir / 'data'`.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    base_dir = Path(meipass) if meipass else Path(__file__).resolve().parent.parent

    created_rel: List[str] = []
    overwritten_rel: List[str] = []
    copied_counts: Dict[str, int] = {}

    if backup_dir is not None and backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
    if backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)

    tar_payload = base_dir / "bundled_payload" / "app_payload.tar.xz"
    if tar_payload.is_file():
        legacy_src = install_dir / "src"
        if legacy_src.is_dir():
            log_line("Backing up and removing legacy uncompiled src/ directory so frozen v2.1 runtime loads cleanly...")
            removed_src_files = 0
            for item in legacy_src.rglob("*"):
                if item.is_file():
                    rel = str(item.relative_to(install_dir)).replace("\\", "/")
                    if backup_dir is not None:
                        bk = backup_dir / rel
                        bk.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, bk)
                    if rel not in overwritten_rel:
                        overwritten_rel.append(rel)
                    removed_src_files += 1
            shutil.rmtree(legacy_src, ignore_errors=True)
            copied_counts["removed_legacy_src"] = removed_src_files

        log_line(f"Extracting compressed application archive -> {install_dir}...")
        copied_counts["payload_archive"] = _extract_tar_payload_with_backup(
            tar_payload,
            install_dir,
            backup_dir,
            created_rel,
            overwritten_rel,
            cancel_event=cancel_event,
            progress_cb=progress_cb,
        )
        # Ensure unpacked packaging manifests are also updated
        for extra_name in ("version.json", "model_manifest.json"):
            cand = base_dir / "packaging" / extra_name
            if not cand.is_file():
                cand = base_dir / extra_name
            if cand.is_file():
                _copy_file_with_backup(
                    cand, install_dir / extra_name, install_dir, backup_dir, created_rel, overwritten_rel
                )
    else:
        bundled_src = base_dir / "src"
        if bundled_src.exists():
            log_line(f"Deploying updated src/ -> {install_dir / 'src'}...")
            copied_counts["src"] = _copy_tree_with_backup(
                bundled_src, install_dir / "src", install_dir, backup_dir, created_rel, overwritten_rel
            )

        bundled_assets = base_dir / "assets"
        if bundled_assets.exists():
            log_line(f"Deploying updated assets/ -> {install_dir / 'assets'}...")
            copied_counts["assets"] = _copy_tree_with_backup(
                bundled_assets, install_dir / "assets", install_dir, backup_dir, created_rel, overwritten_rel
            )

        bundled_licenses = base_dir / "licenses"
        if bundled_licenses.exists():
            copied_counts["licenses"] = _copy_tree_with_backup(
                bundled_licenses, install_dir / "licenses", install_dir, backup_dir, created_rel, overwritten_rel
            )

        for mf_rel in ("version.json", "model_manifest.json"):
            cand = base_dir / mf_rel
            if not cand.is_file():
                cand = base_dir / "packaging" / mf_rel
            if cand.is_file():
                _copy_file_with_backup(
                    cand, install_dir / mf_rel, install_dir, backup_dir, created_rel, overwritten_rel
                )

        bundled_internal = base_dir / "bundled_internal"
        if bundled_internal.exists():
            log_line(f"Deploying updated _internal runtime deltas -> {install_dir / '_internal'}...")
            copied_counts["_internal"] = _copy_tree_with_backup(
                bundled_internal, install_dir / "_internal", install_dir, backup_dir, created_rel, overwritten_rel
            )

        bundled_runtime = base_dir / "bundled_runtime"
        if bundled_runtime.exists():
            log_line(f"Deploying updated runtime/ binaries -> {install_dir / 'runtime'}...")
            copied_counts["runtime"] = _copy_tree_with_backup(
                bundled_runtime, install_dir / "runtime", install_dir, backup_dir, created_rel, overwritten_rel
            )

        bundled_exe = base_dir / "bundled_bin" / EXE_NAME
        if bundled_exe.exists():
            log_line(f"Deploying updated {EXE_NAME} -> {install_dir / EXE_NAME}...")
            _copy_file_with_backup(
                bundled_exe, install_dir / EXE_NAME, install_dir, backup_dir, created_rel, overwritten_rel
            )
            copied_counts["exe"] = 1

    if backup_dir is not None:
        meta = {
            "created_rel": created_rel,
            "overwritten_rel": overwritten_rel,
            "copied_counts": copied_counts,
        }
        (backup_dir / "_backup_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return {
        "installed_any": sum(copied_counts.values()) > 0,
        "copied_counts": copied_counts,
        "created_rel": created_rel,
        "overwritten_rel": overwritten_rel,
    }


def _read_source_version(base_or_install: Path) -> str:
    init_py = base_or_install / "src" / "voxlocal" / "__init__.py"
    if init_py.exists():
        try:
            for line in init_py.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip("\"'")
        except Exception:
            pass
    ver_json = base_or_install / "version.json"
    if ver_json.is_file():
        try:
            return str(json.loads(ver_json.read_text(encoding="utf-8")).get("version") or "")
        except Exception:
            pass
    return ""


def _verify_installed_exe(target_exe: Path, expected_version: str = "") -> Tuple[bool, str]:
    """Run non-destructive runtime self-check against newly installed VoxLocal.exe."""
    if not target_exe.exists():
        return False, f"Executable missing at {target_exe}"
    cmd = [str(target_exe), "--verify-runtime"]
    if expected_version:
        cmd.extend(["--expected-version", expected_version])
    try:
        res = subprocess.run(
            cmd,
            cwd=str(target_exe.parent),
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
        out_txt = (res.stdout or "").strip()
        if res.returncode == 0:
            return True, out_txt or "runtime_verified"
        return False, f"rc={res.returncode} stdout={out_txt} stderr={(res.stderr or '').strip()}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _write_update_status(
    data_dir: Path,
    *,
    status: str,
    from_version: str,
    to_version: str,
    error: str = "",
    target_exe: Optional[Path] = None,
) -> None:
    try:
        sp = data_dir / "logs" / "update_status.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": status,
            "from_version": from_version,
            "to_version": to_version,
            "error": error,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "target_exe": str(target_exe) if target_exe is not None else "",
        }
        sp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        log_line(f"Warning writing update_status.json: {exc}")


def run_uninstall(install_dir: Path, purge_data: bool = False) -> int:
    """Remove VOX binaries, shortcuts, and registry entries while preserving user data by default."""
    install_dir = Path(install_dir).resolve()
    data_dir = _resolve_install_data_dir(install_dir)
    models_dir = _resolve_install_models_dir(install_dir, data_dir)
    is_default_install = install_dir == get_install_dir().resolve()

    _stop_target_vox_processes(install_dir, force_all_if_default=is_default_install)
    if is_default_install:
        register_startup(install_dir / EXE_NAME, enable=False)
        unregister_windows_uninstaller()

        start_menu_lnk = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"
        desktop_lnk = _get_windows_desktop_dir() / f"{APP_NAME}.lnk"
        for lnk in (start_menu_lnk, desktop_lnk, Path.home() / "Desktop" / f"{APP_NAME}.lnk"):
            if lnk.exists():
                try:
                    lnk.unlink()
                except Exception:
                    pass

    if install_dir.exists():
        for item in list(install_dir.iterdir()):
            if item.name.lower() == "data" and not purge_data:
                continue
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
            except Exception:
                pass
        if purge_data:
            shutil.rmtree(install_dir, ignore_errors=True)

    if purge_data:
        for p in (data_dir, models_dir):
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
        vox_root = Path(os.environ.get("LOCALAPPDATA", "")) / "VOX"
        if vox_root.exists() and not any(vox_root.iterdir()):
            shutil.rmtree(vox_root, ignore_errors=True)
    return 0


def run_install(
    silent: bool = False,
    install_dir_override: Optional[Path] = None,
    no_launch: bool = False,
    wait_pid: int = 0,
    relaunch: bool = False,
    expected_version: str = "",
    from_version: str = "",
    provision_models: bool = True,
    create_desktop: bool = True,
    create_start_menu: bool = True,
    enable_autostart: bool = True,
    register_uninstall: bool = False,
    cancel_event: Optional[threading.Event] = None,
    progress_cb: Optional[InstallerProgressCb] = None,
    log_cb: Optional[Callable[[str], None]] = None,
    summary_out: Optional[Dict[str, Any]] = None,
) -> int:
    install_dir = (
        Path(install_dir_override).resolve()
        if install_dir_override is not None
        else get_install_dir().resolve()
    )
    data_dir = _resolve_install_data_dir(install_dir, install_dir_override=install_dir_override)
    models_dir = _resolve_install_models_dir(install_dir, data_dir)

    meipass = getattr(sys, "_MEIPASS", None)
    base_dir = Path(meipass) if meipass else Path(__file__).resolve().parent.parent
    _ensure_import_paths(base_dir)
    _init_installer_logs(install_dir)

    bundled_ver = expected_version or _read_source_version(base_dir) or "2.1.0"
    prev_ver = from_version or _read_source_version(install_dir)

    def _emit(pct: int, stage: str, detail: str, cur_b: int = 0, tot_b: int = 0) -> None:
        if progress_cb is not None:
            try:
                progress_cb(pct, stage, detail, cur_b, tot_b)
            except Exception:
                pass

    log_line(
        f"Starting VOX installer: install_dir='{install_dir}', data_dir='{data_dir}', models_dir='{models_dir}', "
        f"silent={silent}, no_launch={no_launch}, relaunch={relaunch}, wait_pid={wait_pid}, "
        f"from={prev_ver or '?'}, to={bundled_ver or '?'}",
        log_cb,
    )

    try:
        _emit(5, "[1/7] Checking system & stopping active VOX...", "Checking Windows 64-bit compatibility...", 5, 100)
        _stop_target_vox_processes(
            install_dir,
            wait_pid=wait_pid,
            force_all_if_default=(install_dir_override is None),
        )
        if cancel_event is not None and cancel_event.is_set():
            return 130

        install_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        models_dir.mkdir(parents=True, exist_ok=True)
        _init_installer_logs(install_dir)

        _emit(10, "[2/7] Migrating & backing up user data...", "Separating user data into %LOCALAPPDATA%\\VOX and creating SQLite backup...", 10, 100)
        if (install_dir / "data").is_dir() and data_dir.resolve() != (install_dir / "data").resolve():
            try:
                from voxlocal.core.data_migration import migrate_legacy_install_data

                mig_res = migrate_legacy_install_data(
                    install_dir,
                    data_dir.parent,
                    logger_cb=lambda msg: log_line(msg, log_cb),
                )
                if mig_res.get("error"):
                    raise RuntimeError(f"Legacy user-data migration failed: {mig_res['error']}")
                if mig_res.get("migrated"):
                    log_line(f"Legacy install data migrated cleanly to {data_dir}", log_cb)
            except Exception as mig_exc:
                raise RuntimeError(f"Failed migrating existing user data safely: {mig_exc}") from mig_exc

        bk_db = _backup_database_if_exists(data_dir)
        if bk_db:
            log_line(f"Pre-upgrade SQLite backup saved to {bk_db}", log_cb)

        _emit(15, "[3/7] Extracting VOX application files...", "Preparing application binaries and runtimes...", 15, 100)
        backup_dir = install_dir / ".update_backup"

        # If running from source tree without tar_payload or existing _internal, copy local dist/VoxLocal first
        tar_payload = base_dir / "bundled_payload" / "app_payload.tar.xz"
        if not tar_payload.is_file() and not ((install_dir / "_internal").exists() and (install_dir / EXE_NAME).exists()):
            local_dist = Path(__file__).resolve().parent.parent / "dist" / "VoxLocal"
            if local_dist.exists() and (local_dist / EXE_NAME).exists():
                log_line(f"Copying runtime binaries from {local_dist}...", log_cb)
                ignore_fn = shutil.ignore_patterns("data") if (install_dir / "data").exists() else None
                shutil.copytree(local_dist, install_dir, dirs_exist_ok=True, ignore=ignore_fn)

        def _on_extract_progress(done_n: int, tot_n: int, fname: str) -> None:
            pct = 15 + int((done_n * 25) // max(1, tot_n))
            _emit(pct, "[3/7] Extracting VOX application files...", f"Extracting {fname} ({done_n}/{tot_n})", done_n, tot_n)

        deploy_info = install_bundled_components(
            install_dir,
            backup_dir=backup_dir,
            cancel_event=cancel_event,
            progress_cb=_on_extract_progress,
        )

        if cancel_event is not None and cancel_event.is_set():
            _restore_from_backup(install_dir, backup_dir, deploy_info["created_rel"])
            return 130

        target_exe = install_dir / EXE_NAME
        if not target_exe.exists():
            nested = install_dir / "VoxLocal" / EXE_NAME
            if nested.exists():
                target_exe = nested

        # Write install manifest cleanly
        manifest_out = {
            "version": bundled_ver or _read_source_version(install_dir),
            "from_version": prev_ver,
            "installed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_dir": str(data_dir),
            "models_dir": str(models_dir),
            "copied_counts": deploy_info["copied_counts"],
            "exe_sha256": _sha256(target_exe) if target_exe.exists() else "",
        }
        (install_dir / ".install_manifest.json").write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")

        _emit(42, "[4/7] Verifying runtime & binaries...", f"Running VoxLocal.exe --verify-runtime ({bundled_ver})...", 42, 100)
        if (install_dir / "_internal").exists() and target_exe.exists():
            ok_ver, ver_msg = _verify_installed_exe(target_exe, expected_version=bundled_ver)
            log_line(f"Pre-launch runtime verification: ok={ok_ver} detail={ver_msg[:300]}", log_cb)
            if not ok_ver:
                log_line("Pre-launch verification FAILED; rolling back previous version...", log_cb)
                _restore_from_backup(install_dir, backup_dir, deploy_info["created_rel"])
                _write_update_status(
                    data_dir,
                    status="rolled_back",
                    from_version=prev_ver,
                    to_version=bundled_ver,
                    error=ver_msg[:300],
                    target_exe=target_exe,
                )
                if summary_out is not None:
                    summary_out["error"] = ver_msg[:300]
                if not no_launch and target_exe.exists():
                    subprocess.Popen([str(target_exe)], cwd=str(target_exe.parent))
                if not silent:
                    msgbox("VOX Update Rolled Back", f"Update failed verification and was rolled back:\n{ver_msg[:300]}", MB_OK | MB_ICONERROR)
                return 2

        models_provisioned = False
        if provision_models:
            _emit(45, "[5/7] Preparing & downloading required AI models...", "Checking existing verified models...", 0, 100)
            try:
                from voxlocal.engine.model_manifest import get_required_model_ids
                from voxlocal.engine.model_provisioner import ModelProvisioner

                ram_gb = _detect_system_ram_gb()
                selected_ids = get_required_model_ids(ram_total_gb=ram_gb)
                prov = ModelProvisioner(models_dir=models_dir)
                model_idx = {"n": 0}
                total_models = max(1, len(selected_ids))

                def _on_model_prog(mid: str, rel: str, b_done: int, b_tot: int, spd: float, stg: str) -> None:
                    mb_done = b_done / (1024 * 1024)
                    mb_tot = max(1.0, b_tot / (1024 * 1024))
                    spd_mb = spd / (1024 * 1024)
                    frac = float(b_done) / float(max(1, b_tot))
                    pct_overall = min(92, 45 + int(47.0 * min(1.0, (model_idx["n"] + frac) / total_models)))
                    speed_txt = f" ({spd_mb:.1f} MB/s)" if spd_mb > 0.1 else ""
                    detail_txt = f"{mid}: {mb_done:.0f} / {mb_tot:.0f} MB{speed_txt} — {stg}"
                    _emit(pct_overall, "[5/7] Provisioning & verifying local AI models...", detail_txt, b_done, max(1, b_tot))

                all_ok, model_results = prov.provision_required_models(
                    ram_total_gb=ram_gb,
                    include_ids=selected_ids,
                    progress_cb=_on_model_prog,
                    cancel_event=cancel_event,
                    verify_existing_hash=True,
                )
                for m_id, m_res in model_results.items():
                    log_line(f"Model {m_id}: ok={m_res.get('ok')} — {m_res.get('message')}", log_cb)
                    model_idx["n"] += 1
                models_provisioned = bool(all_ok)
                if cancel_event is not None and cancel_event.is_set():
                    return 130
                if not all_ok:
                    err_m = "; ".join(f"{k}: {v.get('message')}" for k, v in model_results.items() if not v.get("ok"))
                    if summary_out is not None:
                        summary_out["error"] = f"Model provisioning incomplete: {err_m}"
                    return 4
            except Exception as exc:
                log_line(f"Model provisioning failed: {exc}", log_cb)
                if summary_out is not None:
                    summary_out["error"] = f"Model provisioning failed: {exc}"
                return 4

        _emit(94, "[6/7] Running installation health checks...", "Verifying installed runtime and model status...", 94, 100)
        if target_exe.is_file():
            hc_res = subprocess.run(
                [str(target_exe), "--health-check"],
                cwd=str(target_exe.parent),
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
            log_line(f"Health check rc={hc_res.returncode}", log_cb)

        _emit(98, "[7/7] Configuring shortcuts & uninstaller...", "Finalizing Start Menu, Desktop & Uninstall registration...", 98, 100)
        default_dir = get_install_dir().resolve()
        if install_dir_override is None or install_dir == default_dir or register_uninstall:
            uninst_exe = install_dir / "Uninstall-VOX.exe"
            if getattr(sys, "frozen", False):
                try:
                    shutil.copy2(Path(sys.executable), uninst_exe)
                except Exception:
                    uninst_exe = target_exe
            register_windows_uninstaller(install_dir, uninst_exe, bundled_ver)

        if install_dir_override is None or install_dir == default_dir:
            start_menu_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if create_start_menu and start_menu_dir.exists():
                create_shortcut(target_exe, start_menu_dir / f"{APP_NAME}.lnk", APP_NAME)
            desktop_dir = _get_windows_desktop_dir()
            if create_desktop and desktop_dir.exists():
                create_shortcut(target_exe, desktop_dir / f"{APP_NAME}.lnk", APP_NAME)
            if target_exe.exists():
                register_startup(target_exe, enable_autostart)

        if wait_pid > 0 or prev_ver != bundled_ver:
            _write_update_status(
                data_dir,
                status="pending_startup_verify",
                from_version=prev_ver,
                to_version=bundled_ver,
                target_exe=target_exe,
            )

        if target_exe.exists() and not no_launch:
            log_line(f"Launching {target_exe}...", log_cb)
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200) | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            proc = subprocess.Popen([str(target_exe)], cwd=str(target_exe.parent), creationflags=flags, close_fds=True)
            time.sleep(3.0)
            rc = proc.poll()
            if rc is not None and rc != 0:
                log_line(f"Launched VoxLocal.exe crashed immediately with rc={rc}; rolling back...", log_cb)
                _restore_from_backup(install_dir, backup_dir, deploy_info["created_rel"])
                _write_update_status(
                    data_dir,
                    status="rolled_back",
                    from_version=prev_ver,
                    to_version=bundled_ver,
                    error=f"Startup crash rc={rc}",
                    target_exe=target_exe,
                )
                subprocess.Popen([str(target_exe)], cwd=str(target_exe.parent))
                return 3
            log_line("Launched updated VoxLocal.exe successfully.", log_cb)

        if summary_out is not None:
            summary_out["version"] = bundled_ver
            summary_out["install_dir"] = str(install_dir)
            summary_out["data_dir"] = str(data_dir)
            summary_out["models_dir"] = str(models_dir)
            summary_out["models_provisioned"] = models_provisioned

        _emit(100, "Installation Complete", "VOX installed cleanly.", 100, 100)
        return 0
    except InterruptedError:
        log_line("Installation cancelled by user.", log_cb)
        return 130
    except Exception as exc:
        log_line(f"Installation failed with exception: {exc}", log_cb)
        if summary_out is not None:
            summary_out["error"] = str(exc)
        if not silent:
            msgbox("VOX Setup Error", f"Installation failed:\n{exc}", MB_OK | MB_ICONERROR)
        return 1


def main() -> int:
    meipass = getattr(sys, "_MEIPASS", None)
    base = Path(meipass) if meipass else Path(__file__).resolve().parent.parent
    _ensure_import_paths(base)

    if "--smoke-test" in sys.argv:
        p = get_install_dir()
        has_payload = (base / "bundled_payload" / "app_payload.tar.xz").is_file() or (
            (base / "src" / "voxlocal" / "core" / "entitlement.py").exists()
        )
        has_manifest = (base / "packaging" / "model_manifest.json").is_file() or (base / "model_manifest.json").is_file()
        print(f"SMOKE_OK install_dir={p} has_payload={has_payload} has_manifest={has_manifest}")
        return 0
    if "--version" in sys.argv:
        ver = _read_source_version(base) or "2.1.0"
        print(ver)
        return 0

    opts = parse_cli_args(sys.argv[1:])

    if opts["uninstall"]:
        target = opts["install_dir_override"] or get_install_dir()
        if opts["silent"]:
            return run_uninstall(target, purge_data=bool(opts["purge_data"]))
        from installer_wizard import run_uninstall_gui

        return run_uninstall_gui(
            install_dir=target,
            version=_read_source_version(base) or "2.1.0",
            uninstall_fn=run_uninstall,
        )

    if opts["gui_screenshot_dir"] is not None or not opts["silent"]:
        from installer_wizard import run_gui_wizard

        return run_gui_wizard(
            version=str(opts["expected_version"] or _read_source_version(base) or "2.1.0"),
            default_install_dir=opts["install_dir_override"] or get_install_dir(),
            runner_fn=run_install,
            screenshot_dir=opts["gui_screenshot_dir"],
        )

    return run_install(
        silent=True,
        install_dir_override=opts["install_dir_override"],
        no_launch=bool(opts["no_launch"]),
        wait_pid=int(opts["wait_pid"]),
        relaunch=bool(opts["relaunch"]),
        expected_version=str(opts["expected_version"]),
        from_version=str(opts["from_version"]),
        provision_models=not bool(opts["skip_models"]),
        register_uninstall=bool(opts["register_uninstall"]),
    )


if __name__ == "__main__":
    sys.exit(main())

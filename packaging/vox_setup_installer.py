"""
VOX Voice Assistant — Resilient Native Windows Installer & In-Place Updater (VoxLocal-Setup.exe).
Supports:
- Clean installations into %LOCALAPPDATA%\\Programs\\VOX (or custom --install-dir, including paths with spaces)
- Atomic in-place upgrades updating VoxLocal.exe, src/, assets/, and _internal/ runtime deltas without touching data/
- Graceful --wait-pid teardown, file-unlock retry, pre-launch --verify-runtime checks, and automatic rollback
- Persistent installer logs and post-launch startup verification handshake
"""

from __future__ import annotations

import ctypes
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import zipfile

RELEASE_ZIP_URL = "https://github.com/milinddwivedi/Vox/releases/latest/download/VoxLocal-Portable-win64.zip"
APP_NAME = "VOX Voice Assistant"
EXE_NAME = "VoxLocal.exe"

MB_OK = 0x00000000
MB_OKCANCEL = 0x00000001
MB_ICONINFORMATION = 0x00000040
MB_ICONERROR = 0x00000010
IDOK = 1

_LOG_PATHS: List[Path] = []


def _init_installer_logs(install_dir: Optional[Path] = None) -> None:
    candidates: List[Path] = [Path(tempfile.gettempdir()) / "vox_updater" / "vox_installer.log"]
    if install_dir is not None:
        candidates.append(Path(install_dir) / "data" / "logs" / "vox_installer.log")
    for p in candidates:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p not in _LOG_PATHS:
                _LOG_PATHS.append(p)
        except Exception:
            pass


def log_line(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        print(line, flush=True)
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


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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


def parse_cli_args(argv: List[str]) -> Dict[str, Any]:
    """Parse installer arguments, safely recovering unquoted space-split --install-dir paths."""
    silent = False
    no_launch = False
    relaunch = False
    wait_pid = 0
    expected_version = ""
    from_version = ""
    install_dir_override: Optional[Path] = None

    known_flags = {
        "--silent", "-s", "/s", "/silent", "/verysilent", "/suppressmsgboxes", "/norestart", "/sp-",
        "--no-launch", "--relaunch", "--install-dir", "--wait-pid", "--expected-version", "--from-version",
        "--smoke-test", "--version",
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
        "wait_pid": wait_pid,
        "expected_version": expected_version,
        "from_version": from_version,
        "install_dir_override": install_dir_override,
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
                ["taskkill", "/F", "/PID", str(int(pid_val)) if (pid_val := wait_pid) else "0"],
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
            time.sleep(0.6)

    # Terminate any lingering VoxLocal.exe running specifically inside install_dir
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


def install_bundled_components(
    install_dir: Path,
    backup_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Atomically deploys bundled VoxLocal.exe, src/, assets/, and _internal/ deltas
    while backing up replaced files and never touching install_dir / 'data'.
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

    bundled_internal = base_dir / "bundled_internal"
    if bundled_internal.exists():
        log_line(f"Deploying updated _internal runtime deltas -> {install_dir / '_internal'}...")
        copied_counts["_internal"] = _copy_tree_with_backup(
            bundled_internal, install_dir / "_internal", install_dir, backup_dir, created_rel, overwritten_rel
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


def _extract_zip_preserving_data(zf: zipfile.ZipFile, install_dir: Path) -> None:
    """Extract archive while never deleting or overwriting existing files in install_dir / 'data'."""
    for member in zf.infolist():
        norm_name = member.filename.replace("\\", "/").lstrip("/")
        if norm_name == "data" or norm_name.startswith("data/"):
            dest_path = install_dir / norm_name
            if dest_path.exists():
                continue
        zf.extract(member, install_dir)


def _read_source_version(base_or_install: Path) -> str:
    init_py = base_or_install / "src" / "voxlocal" / "__init__.py"
    if init_py.exists():
        try:
            for line in init_py.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip("\"'")
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
    install_dir: Path,
    *,
    status: str,
    from_version: str,
    to_version: str,
    error: str = "",
) -> None:
    try:
        sp = install_dir / "data" / "logs" / "update_status.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": status,
            "from_version": from_version,
            "to_version": to_version,
            "error": error,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "target_exe": str(install_dir / EXE_NAME),
        }
        sp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        log_line(f"Warning writing update_status.json: {exc}")


def run_install(
    silent: bool = False,
    install_dir_override: Optional[Path] = None,
    no_launch: bool = False,
    wait_pid: int = 0,
    relaunch: bool = False,
    expected_version: str = "",
    from_version: str = "",
) -> int:
    install_dir = (
        Path(install_dir_override).resolve()
        if install_dir_override is not None
        else get_install_dir().resolve()
    )
    _init_installer_logs(install_dir)
    meipass = getattr(sys, "_MEIPASS", None)
    base_dir = Path(meipass) if meipass else Path(__file__).resolve().parent.parent
    bundled_ver = expected_version or _read_source_version(base_dir)
    prev_ver = from_version or _read_source_version(install_dir)

    log_line(
        f"Starting VOX installer: install_dir='{install_dir}', silent={silent}, "
        f"no_launch={no_launch}, relaunch={relaunch}, wait_pid={wait_pid}, "
        f"from={prev_ver or '?'}, to={bundled_ver or '?'}"
    )

    if not silent:
        resp = msgbox(
            "VOX Voice Assistant — Windows Setup",
            f"Install VOX Voice Assistant to:\n{install_dir}\n\n"
            "• Creates Desktop & Start Menu shortcuts\n"
            "• Preserves your local recordings & transcripts\n"
            "• Launches VOX automatically when ready\n\n"
            "Click OK to begin installation.",
            MB_OKCANCEL | MB_ICONINFORMATION,
        )
        if resp != IDOK:
            return 0

    try:
        log_line("[1/4] Stopping active VOX instance in target directory...")
        _stop_target_vox_processes(
            install_dir,
            wait_pid=wait_pid,
            force_all_if_default=(install_dir_override is None),
        )
        install_dir.mkdir(parents=True, exist_ok=True)
        _init_installer_logs(install_dir)

        log_line("[2/4] Preparing runtime & preserving data/ ...")
        if (install_dir / "_internal").exists() and (install_dir / EXE_NAME).exists():
            log_line("Existing _internal and VoxLocal.exe detected; applying atomic incremental update + runtime deltas.")
        else:
            exe_dir = Path(sys.executable).resolve().parent
            local_zip = exe_dir / "VoxLocal-Portable-win64.zip"
            tmp_zip = None

            if local_zip.exists():
                log_line(f"Extracting local runtime package: {local_zip}")
                with zipfile.ZipFile(local_zip, "r") as zf:
                    _extract_zip_preserving_data(zf, install_dir)
            else:
                local_dist = exe_dir / "dist" / "VoxLocal"
                if not local_dist.exists():
                    local_dist = Path(__file__).resolve().parent.parent / "dist" / "VoxLocal"

                if local_dist.exists() and (local_dist / EXE_NAME).exists():
                    log_line(f"Copying runtime binaries from {local_dist}...")
                    ignore_fn = shutil.ignore_patterns("data") if (install_dir / "data").exists() else None
                    shutil.copytree(local_dist, install_dir, dirs_exist_ok=True, ignore=ignore_fn)
                else:
                    log_line("Downloading base VOX runtime package from GitHub Releases...")
                    try:
                        fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="vox_setup_")
                        os.close(fd)
                        tmp_zip = Path(tmp_name)
                        req = urllib.request.Request(
                            RELEASE_ZIP_URL,
                            headers={"User-Agent": "VOX-Windows-Installer/1.0"},
                        )
                        with urllib.request.urlopen(req, timeout=180) as resp_http, open(tmp_zip, "wb") as out_f:
                            while chunk := resp_http.read(256 * 1024):
                                out_f.write(chunk)
                        with zipfile.ZipFile(tmp_zip, "r") as zf:
                            _extract_zip_preserving_data(zf, install_dir)
                    except Exception as dl_err:
                        log_line(f"Network runtime download skipped/failed: {dl_err}")
            if tmp_zip and tmp_zip.exists():
                try:
                    tmp_zip.unlink()
                except Exception:
                    pass

        log_line("[3/4] Installing latest VoxLocal.exe, src/, assets/, and _internal/ deltas...")
        backup_dir = install_dir / ".update_backup"
        deploy_info = install_bundled_components(install_dir, backup_dir=backup_dir)

        target_exe = install_dir / EXE_NAME
        if not target_exe.exists():
            nested = install_dir / "VoxLocal" / EXE_NAME
            if nested.exists():
                target_exe = nested

        # Write install manifest before verifying
        manifest_out = {
            "version": bundled_ver or _read_source_version(install_dir),
            "from_version": prev_ver,
            "installed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "copied_counts": deploy_info["copied_counts"],
            "exe_sha256": _sha256(target_exe) if target_exe.exists() else "",
        }
        (install_dir / ".install_manifest.json").write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")

        if (install_dir / "_internal").exists() and target_exe.exists():
            ok_ver, ver_msg = _verify_installed_exe(target_exe, expected_version=bundled_ver)
            log_line(f"Pre-launch runtime verification: ok={ok_ver} detail={ver_msg[:300]}")
            if not ok_ver:
                log_line("Pre-launch verification FAILED; rolling back previous version...")
                _restore_from_backup(install_dir, backup_dir, deploy_info["created_rel"])
                _write_update_status(
                    install_dir,
                    status="rolled_back",
                    from_version=prev_ver,
                    to_version=bundled_ver,
                    error=ver_msg[:300],
                )
                if not no_launch and target_exe.exists():
                    subprocess.Popen([str(target_exe)], cwd=str(target_exe.parent))
                if not silent:
                    msgbox("VOX Update Rolled Back", f"Update failed verification and was rolled back:\n{ver_msg[:300]}", MB_OK | MB_ICONERROR)
                return 2

        log_line("[4/4] Updating shortcuts & launching VOX...")
        default_dir = get_install_dir().resolve()
        if install_dir_override is None or install_dir == default_dir:
            start_menu_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if start_menu_dir.exists():
                create_shortcut(target_exe, start_menu_dir / f"{APP_NAME}.lnk", APP_NAME)
            desktop_dir = Path.home() / "Desktop"
            if desktop_dir.exists():
                create_shortcut(target_exe, desktop_dir / f"{APP_NAME}.lnk", APP_NAME)
            if target_exe.exists():
                register_startup(target_exe, True)

        if wait_pid > 0 or prev_ver != bundled_ver:
            _write_update_status(
                install_dir,
                status="pending_startup_verify",
                from_version=prev_ver,
                to_version=bundled_ver,
            )

        if target_exe.exists() and not no_launch:
            log_line(f"Launching {target_exe}...")
            proc = subprocess.Popen([str(target_exe)], cwd=str(target_exe.parent))
            time.sleep(3.0)
            rc = proc.poll()
            if rc is not None and rc != 0:
                log_line(f"Launched VoxLocal.exe crashed immediately with rc={rc}; rolling back...")
                _restore_from_backup(install_dir, backup_dir, deploy_info["created_rel"])
                _write_update_status(
                    install_dir,
                    status="rolled_back",
                    from_version=prev_ver,
                    to_version=bundled_ver,
                    error=f"Startup crash rc={rc}",
                )
                subprocess.Popen([str(target_exe)], cwd=str(target_exe.parent))
                return 3
            log_line("Launched updated VoxLocal.exe successfully.")

        if not silent:
            msgbox(
                "VOX Installed Successfully",
                f"VOX Voice Assistant v{bundled_ver or ''} has been installed to:\n{install_dir}\n\n"
                "Your recordings, transcripts, and settings have been preserved!",
                MB_OK | MB_ICONINFORMATION,
            )
        return 0
    except Exception as exc:
        log_line(f"Installation failed with exception: {exc}")
        if not silent:
            msgbox("VOX Setup Error", f"Installation failed:\n{exc}", MB_OK | MB_ICONERROR)
        return 1


def main() -> int:
    meipass = getattr(sys, "_MEIPASS", None)
    base = Path(meipass) if meipass else Path(__file__).resolve().parent.parent

    if "--smoke-test" in sys.argv:
        p = get_install_dir()
        src_path = base / "src"
        has_src = src_path.exists() and (src_path / "voxlocal" / "core" / "entitlement.py").exists()
        has_int = (base / "bundled_internal" / "sherpa_onnx").exists()
        print(f"SMOKE_OK install_dir={p} has_bundled_src={has_src} has_bundled_sherpa={has_int}")
        return 0
    if "--version" in sys.argv:
        ver = _read_source_version(base) or "1.0.13"
        print(ver)
        return 0

    opts = parse_cli_args(sys.argv[1:])
    return run_install(
        silent=bool(opts["silent"]),
        install_dir_override=opts["install_dir_override"],
        no_launch=bool(opts["no_launch"]),
        wait_pid=int(opts["wait_pid"]),
        relaunch=bool(opts["relaunch"]),
        expected_version=str(opts["expected_version"]),
        from_version=str(opts["from_version"]),
    )


if __name__ == "__main__":
    sys.exit(main())

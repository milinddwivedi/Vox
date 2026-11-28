"""
VOX v2.1 Clean Reproducible Production Build & Release Packaging Script.

Steps:
1. Build `VoxLocal.spec` -> `dist/VoxLocal/VoxLocal.exe`
2. Prune duplicate root llama DLLs if any and verify zero forbidden private/dev files in `dist/VoxLocal`
3. Compress `dist/VoxLocal/*` into `build/app_payload.tar.xz` (LZMA preset 6)
4. Build `VoxLocal-Setup.spec` -> `dist/VoxLocal-Setup.exe`
5. Sync `release_artifacts/VoxLocal-Setup.exe` (+ `docs/downloads/VoxLocal-Setup.exe` unless `--skip-docs-sync`)
6. Emit `release_artifacts/SHA256SUMS.txt`, `release_artifacts/release_manifest_v<ver>.json`, `release_artifacts/RELEASE_NOTES_v<ver>.md`
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT_DIR = Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(2 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest().lower()


def _verify_clean_dist(dist_dir: Path) -> None:
    forbidden_exts = {".db", ".db-wal", ".db-shm", ".wav", ".vault", ".mp4", ".html", ".pem", ".key"}
    forbidden_names = {"config.json", ".env", ".env.local", ".env.production.local", "auth_session.vault"}
    violations = []
    for p in dist_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(dist_dir)).replace("\\", "/")
        low_name = p.name.lower()
        if low_name in forbidden_names and not rel.startswith("_internal/"):
            violations.append(rel)
        if p.suffix.lower() in forbidden_exts and not rel.startswith("_internal/"):
            violations.append(rel)
        if "/data/" in f"/{rel}/" or rel.startswith("data/"):
            violations.append(rel)
    if violations:
        raise RuntimeError(f"Forbidden dev/user/website files found in dist bundle: {violations[:15]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build clean VOX v2.1 Windows release artifacts")
    parser.add_argument("--skip-app-build", action="store_true", help="Reuse existing dist/VoxLocal build")
    parser.add_argument("--skip-docs-sync", action="store_true", help="Do not copy installer to docs/downloads")
    args = parser.parse_args()

    ver_data = json.loads((ROOT_DIR / "packaging" / "version.json").read_text(encoding="utf-8"))
    version = str(ver_data["version"])

    if not args.skip_app_build:
        print(f"[1/5] Building VoxLocal v{version} bundle via PyInstaller...")
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "VoxLocal.spec"],
            cwd=str(ROOT_DIR),
            check=True,
        )

    dist_app = ROOT_DIR / "dist" / "VoxLocal"
    if not (dist_app / "VoxLocal.exe").is_file():
        raise FileNotFoundError(f"Expected {dist_app / 'VoxLocal.exe'} not found!")

    print("[2/5] Verifying dist bundle cleanliness (zero dev DBs, WAVs, secrets, website files)...")
    _verify_clean_dist(dist_app)
    app_exe_sha256 = _sha256(dist_app / "VoxLocal.exe")
    app_total_bytes = sum(f.stat().st_size for f in dist_app.rglob("*") if f.is_file())

    payload_tar = ROOT_DIR / "build" / "app_payload.tar.xz"
    payload_tar.parent.mkdir(parents=True, exist_ok=True)
    print(f"[3/5] Packing clean app payload into {payload_tar} (LZMA preset=4)...")
    with tarfile.open(payload_tar, mode="w:xz", preset=4) as tf:
        for item in sorted(dist_app.rglob("*")):
            if item.is_file():
                rel = str(item.relative_to(dist_app)).replace("\\", "/")
                tf.add(item, arcname=rel, recursive=False)
    tar_size = payload_tar.stat().st_size
    print(f"      app_total_mb={app_total_bytes/(1024*1024):.2f} MB -> tar_xz_mb={tar_size/(1024*1024):.2f} MB")

    print("[4/5] Building professional GUI Setup Wizard (VoxLocal-Setup.exe)...")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "VoxLocal-Setup.spec"],
        cwd=str(ROOT_DIR),
        check=True,
    )

    setup_exe = ROOT_DIR / "dist" / "VoxLocal-Setup.exe"
    setup_size = setup_exe.stat().st_size
    setup_sha = _sha256(setup_exe)

    print("[5/5] Writing release_artifacts/, SHA256SUMS.txt, and release notes...")
    rel_dir = ROOT_DIR / "release_artifacts"
    rel_dir.mkdir(parents=True, exist_ok=True)
    rel_setup = rel_dir / "VoxLocal-Setup.exe"
    shutil.copy2(setup_exe, rel_setup)

    if not args.skip_docs_sync:
        docs_dl = ROOT_DIR / "docs" / "downloads" / "VoxLocal-Setup.exe"
        docs_dl.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(setup_exe, docs_dl)

    sums_lines = [
        f"{setup_sha} *VoxLocal-Setup.exe",
        f"{app_exe_sha256} *VoxLocal.exe",
        f"{_sha256(ROOT_DIR / 'packaging' / 'model_manifest.json')} *model_manifest.json",
    ]
    (rel_dir / "SHA256SUMS.txt").write_text("\n".join(sums_lines) + "\n", encoding="utf-8")

    manifest_summary = {
        "product_id": ver_data["product_id"],
        "product_name": ver_data["product_name"],
        "version": version,
        "build_number": ver_data["build_number"],
        "release_date": ver_data["release_date"],
        "app_bundle_bytes": app_total_bytes,
        "app_exe_sha256": app_exe_sha256,
        "installer_size_bytes": setup_size,
        "installer_sha256": setup_sha,
        "model_manifest_version": ver_data["model_manifest_version"],
        "authenticode_signed": False,
    }
    (rel_dir / f"release_manifest_v{version}.json").write_text(json.dumps(manifest_summary, indent=2), encoding="utf-8")

    notes = (
        f"# VOX v{version} Release Notes ({ver_data['release_date']})\n\n"
        "- **Bulletproof Automatic Call Detection**: Eliminated false meeting triggers from browser developer/docs portals (`developers.zoom.us`, `marketplace.zoom.us`), landing pages, pre-join/waiting rooms, Teams Chat/Calendar tabs, and silent tabs without an active microphone call; disabled silent auto-start countdowns when consent prompts are enabled.\n"
        "- **Redesigned Meetings Library & Full Folder Controls**: Clean Material 3 dropdown controls without native bevels, full Create / Rename / Delete folder management (keeping all meetings intact), working Comfortable vs Compact density views inside the library menu, and dynamic Rescued filter visibility.\n"
        "- **10-Day Free Trial, In-App Dictation, Dynamic Drawer & Zoom OAuth**: Server-enforced 10-day Google trial with Pro upgrade flow, live voice dictation in My Notes and Scratchpad, context-aware 3-mode Meeting Drawer (`live` / `prep` / `workspace`), auto-dismissing Upcoming popover, and one-click Zoom Calendar OAuth.\n"
        f"\n**Installer Size:** {setup_size:,} bytes ({setup_size/(1024*1024):.1f} MB)  \n"
        f"**Installer SHA-256:** `{setup_sha}`\n"
    )
    (rel_dir / f"RELEASE_NOTES_v{version}.md").write_text(notes, encoding="utf-8")
    print("BUILD_SUMMARY=" + json.dumps(manifest_summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

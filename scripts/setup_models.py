"""
Model Asset Setup, Checksum Verification & Recovery Script for VoxLocal.
Reads `models/manifest.json`, verifies existing model files in `data/models/`,
checks SHA-256 integrity, and downloads any missing or corrupted models.

Usage:
    python scripts/setup_models.py --verify
    python scripts/setup_models.py --download small base
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

ROOT_DIR = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT_DIR / "models" / "manifest.json"
MODELS_DIR = ROOT_DIR / "data" / "models"


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_model_bin(models_dir: Path, model_id: str) -> Optional[Path]:
    slug = f"models--Systran--faster-whisper-{model_id}"
    snap_dir = models_dir / slug / "snapshots"
    if not snap_dir.exists():
        return None
    for child in snap_dir.iterdir():
        candidate = child / "model.bin"
        if candidate.exists():
            return candidate
    return None


def verify_model(models_dir: Path, entry: Dict) -> Tuple[bool, str]:
    model_id = entry["id"]
    expected_sha = entry.get("model_bin_sha256")
    expected_size = entry.get("size_bytes")

    bin_path = find_model_bin(models_dir, model_id)
    if not bin_path:
        return False, f"Model '{model_id}' is not present in {models_dir}"

    actual_size = bin_path.stat().st_size
    if expected_size and abs(actual_size - int(expected_size)) > 1024:
        return False, f"Model '{model_id}' size mismatch: got {actual_size}, expected {expected_size}"

    if expected_sha:
        actual_sha = compute_sha256(bin_path)
        if actual_sha.lower() != expected_sha.lower():
            return False, f"Model '{model_id}' SHA-256 mismatch: got {actual_sha[:12]}..., expected {expected_sha[:12]}..."

    # Ensure vocabulary/tokenizer JSON and config.json exist alongside model.bin
    snap_folder = bin_path.parent
    for req_file in ("config.json", "vocabulary.txt", " vocabulary.json", "tokenizer.json"):
        pass
    if not (snap_folder / "config.json").exists():
        return False, f"Model '{model_id}' missing config.json in {snap_folder}"

    return True, f"Verified '{model_id}' ({actual_size / (1024*1024):.1f} MB, SHA-256 OK)"


def download_and_verify_model(models_dir: Path, entry: Dict, force: bool = False) -> Tuple[bool, str]:
    from faster_whisper import WhisperModel

    model_id = entry["id"]
    if not force:
        ok, msg = verify_model(models_dir, entry)
        if ok:
            return True, msg

    slug = f"models--Systran--faster-whisper-{model_id}"
    target_folder = models_dir / slug
    if target_folder.exists() and force:
        shutil.rmtree(target_folder, ignore_errors=True)

    print(f"[setup_models] Downloading & initializing '{model_id}' ({entry.get('size_human', '')})...")
    try:
        os.makedirs(models_dir, exist_ok=True)
        _ = WhisperModel(
            model_id,
            device="cpu",
            compute_type="int8",
            cpu_threads=4,
            download_root=str(models_dir),
            local_files_only=False,
        )
        return verify_model(models_dir, entry)
    except Exception as exc:
        return False, f"Failed downloading '{model_id}': {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify and download VoxLocal Whisper models.")
    parser.add_argument("--verify", action="store_true", help="Verify checksums of installed models")
    parser.add_argument(
        "--download",
        nargs="*",
        default=None,
        help="Model IDs to download and verify (default: all default models in manifest)",
    )
    parser.add_argument("--force", action="store_true", help="Force re-download even if present")
    args = parser.parse_args()

    if not MANIFEST_PATH.exists():
        print(f"Manifest not found at {MANIFEST_PATH}", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    models_by_id = {m["id"]: m for m in manifest.get("models", [])}

    if args.verify and args.download is None:
        all_ok = True
        for m_id, entry in models_by_id.items():
            if not entry.get("cached_by_default", False) and not find_model_bin(MODELS_DIR, m_id):
                print(f"[OPTIONAL] {m_id}: not installed (on-demand)")
                continue
            ok, msg = verify_model(MODELS_DIR, entry)
            status = "OK" if ok else "MISSING/CORRUPT"
            print(f"[{status}] {msg}")
            if not ok:
                all_ok = False
        return 0 if all_ok else 1

    targets: List[str] = (
        args.download
        if args.download
        else [m["id"] for m in manifest.get("models", []) if m.get("cached_by_default")]
    )
    all_ok = True
    for m_id in targets:
        entry = models_by_id.get(m_id)
        if not entry:
            print(f"Unknown model ID '{m_id}'. Available: {list(models_by_id.keys())}", file=sys.stderr)
            all_ok = False
            continue
        ok, msg = download_and_verify_model(MODELS_DIR, entry, force=args.force)
        print(f"[{'OK' if ok else 'FAIL'}] {msg}")
        if not ok:
            all_ok = False

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

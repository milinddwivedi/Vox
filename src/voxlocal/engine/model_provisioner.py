"""
VOX Resilient Model Provisioner.

Shared verification and streaming downloader used by:
- Windows Installer GUI & Setup bootstrapper (`packaging/vox_setup_installer.py`)
- Desktop `MeetingModelManager` & Settings repair tools

Features:
- Canonical SHA-256 verification against `packaging/model_manifest.json`
- Automatic reuse of already-verified models on disk (avoiding duplicate multi-GB downloads)
- HTTP Range resume via `.part` staging files and retry with exponential backoff
- Content-Length & cryptographic SHA-256 verification before atomic activation
- Free disk space validation and clean cancellation support
"""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from voxlocal.engine.model_manifest import get_model_entries, get_required_model_ids, load_model_manifest

# Progress callback: (model_id, file_rel, bytes_done, bytes_total, speed_bps, stage_text)
ProvisionProgressCb = Callable[[str, str, int, int, float, str], None]


def compute_file_sha256(path: Path, cancel_event: Optional[threading.Event] = None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Verification cancelled by user")
            chunk = fh.read(2 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def _discover_candidate_model_roots(primary_models_dir: Path) -> List[Path]:
    """Return ordered list of existing model roots to inspect for reusable verified models."""
    roots: List[Path] = [Path(primary_models_dir)]
    local_app = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app:
        roots.append(Path(local_app) / "VOX" / "models")
        roots.append(Path(local_app) / "Programs" / "VOX" / "data" / "models")
    # Also check legacy huggingface cache for Whisper CTranslate2 snapshots
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")

    deduped: List[Path] = []
    seen: set[str] = set()
    for r in roots:
        try:
            key = str(r.resolve()).lower()
        except Exception:
            key = str(r).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped


def _ensure_hf_ref_pointer(target_dir: Path, spec: Dict[str, Any]) -> None:
    """Create `refs/main` pointing to the snapshot revision for HF-slugged Whisper caches."""
    slug = str(spec.get("hf_cache_slug") or "").strip()
    rev = str(spec.get("revision") or "").strip()
    if not slug or not rev:
        return
    # target_dir is .../<slug>/snapshots/<rev>
    try:
        cache_root = target_dir.parent.parent
        if cache_root.name == slug:
            refs_dir = cache_root / "refs"
            refs_dir.mkdir(parents=True, exist_ok=True)
            (refs_dir / "main").write_text(rev, encoding="utf-8")
    except Exception:
        pass


class ModelProvisioner:
    """Verify, reuse, and securely download canonical VOX AI models."""

    def __init__(
        self,
        models_dir: str | Path,
        manifest_path: Optional[str] = None,
    ) -> None:
        self.models_dir = Path(models_dir)
        self.manifest = load_model_manifest(manifest_path)
        self.entries = get_model_entries(manifest_path)

    def get_model_dir(self, model_id: str, base_dir: Optional[Path] = None) -> Path:
        spec = self.entries[model_id]
        root = Path(base_dir) if base_dir is not None else self.models_dir
        rel = str(spec["install_rel_dir"]).replace("\\", "/")
        return root / rel

    def inspect_model(
        self,
        model_id: str,
        *,
        verify_hash: bool = False,
        base_dir: Optional[Path] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        spec = self.entries[model_id]
        target_dir = self.get_model_dir(model_id, base_dir=base_dir)
        missing: List[str] = []
        corrupt: List[str] = []
        actual_bytes = 0

        for fspec in spec.get("files", []):
            rel = str(fspec["rel_path"])
            exp_size = int(fspec["size"])
            exp_sha = str(fspec["sha256"]).lower().strip()
            fp = target_dir / rel
            if not fp.is_file():
                missing.append(rel)
                continue
            sz = fp.stat().st_size
            actual_bytes += sz
            if sz != exp_size:
                corrupt.append(f"{rel} (size {sz} != {exp_size})")
                continue
            if verify_hash and exp_sha:
                digest = compute_file_sha256(fp, cancel_event=cancel_event)
                if not hmac.compare_digest(digest, exp_sha):
                    corrupt.append(f"{rel} (sha256 mismatch)")

        installed = (len(missing) == 0) and (len(corrupt) == 0)
        if installed:
            _ensure_hf_ref_pointer(target_dir, spec)
        return {
            "id": model_id,
            "display_name": spec["display_name"],
            "version": spec["version"],
            "revision": spec["revision"],
            "license": spec["license"],
            "runtime": spec["runtime"],
            "feature": spec["feature"],
            "required": bool(spec.get("required", False)),
            "path": str(target_dir),
            "installed": installed,
            "verified": installed and verify_hash,
            "missing_files": missing,
            "corrupt_files": corrupt,
            "size_bytes": actual_bytes,
            "expected_size_bytes": int(spec.get("expected_size_bytes") or 0),
        }

    def try_reuse_existing_model(
        self,
        model_id: str,
        *,
        verify_hash: bool = True,
        cancel_event: Optional[threading.Event] = None,
    ) -> Tuple[bool, str]:
        """
        Check other standard VOX model locations on disk and link/copy verified files
        into `self.models_dir` so upgrades/reinstalls avoid multi-GB re-downloads.
        """
        primary_status = self.inspect_model(model_id, verify_hash=verify_hash, cancel_event=cancel_event)
        if primary_status["installed"]:
            return True, f"Verified existing {model_id} in {self.models_dir}"

        target_dir = self.get_model_dir(model_id)
        for root in _discover_candidate_model_roots(self.models_dir):
            try:
                if root.resolve() == self.models_dir.resolve():
                    continue
            except Exception:
                pass
            cand_dir = self.get_model_dir(model_id, base_dir=root)
            if not cand_dir.is_dir():
                continue
            st = self.inspect_model(model_id, verify_hash=verify_hash, base_dir=root, cancel_event=cancel_event)
            if st["installed"]:
                target_dir.mkdir(parents=True, exist_ok=True)
                for fspec in self.entries[model_id]["files"]:
                    rel = str(fspec["rel_path"])
                    src_f = cand_dir / rel
                    dst_f = target_dir / rel
                    dst_f.parent.mkdir(parents=True, exist_ok=True)
                    tmp_f = dst_f.with_name(dst_f.name + ".reuse_tmp")
                    shutil.copy2(src_f, tmp_f)
                    os.replace(tmp_f, dst_f)
                _ensure_hf_ref_pointer(target_dir, self.entries[model_id])
                return True, f"Reused verified {model_id} from {cand_dir}"
        return False, ""

    def _download_single_file(
        self,
        model_id: str,
        fspec: Dict[str, Any],
        dest_path: Path,
        *,
        prior_bytes: int,
        model_total_bytes: int,
        progress_cb: Optional[ProvisionProgressCb] = None,
        cancel_event: Optional[threading.Event] = None,
        max_retries: int = 3,
        timeout_sec: int = 45,
    ) -> Tuple[bool, str]:
        url = str(fspec["url"])
        rel = str(fspec["rel_path"])
        exp_size = int(fspec["size"])
        exp_sha = str(fspec["sha256"]).lower().strip()

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = dest_path.with_name(dest_path.name + ".part")

        # Fast-path: already valid destination file
        if dest_path.is_file() and dest_path.stat().st_size == exp_size:
            if not exp_sha or hmac.compare_digest(compute_file_sha256(dest_path, cancel_event=cancel_event), exp_sha):
                return True, "already_verified"

        attempt = 0
        last_err = ""
        while attempt < max_retries:
            attempt += 1
            if cancel_event is not None and cancel_event.is_set():
                return False, "Download cancelled by user"
            try:
                existing = part_path.stat().st_size if part_path.is_file() else 0
                if existing > exp_size:
                    part_path.unlink(missing_ok=True)
                    existing = 0

                headers = {"User-Agent": "VOX-ModelProvisioner/2.1"}
                if 0 < existing < exp_size:
                    headers["Range"] = f"bytes={existing}-"

                req = urllib.request.Request(url, headers=headers)
                t_start = time.monotonic()
                session_bytes = 0

                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    status = int(getattr(resp, "status", 200) or 200)
                    # If server ignored Range and returned full 200, restart clean
                    if existing > 0 and status == 200:
                        existing = 0
                    cl_raw = resp.headers.get("Content-Length") if resp.headers else None
                    if cl_raw and status == 200:
                        remote_len = int(cl_raw)
                        if exp_size > 0 and remote_len != exp_size:
                            raise ValueError(f"Content-Length mismatch for {rel}: {remote_len} != {exp_size}")

                    mode = "ab" if (existing > 0 and status == 206) else "wb"
                    cur_done = existing if mode == "ab" else 0
                    last_emit = 0.0
                    with part_path.open(mode) as fh:
                        while True:
                            if cancel_event is not None and cancel_event.is_set():
                                return False, "Download cancelled by user"
                            chunk = resp.read(512 * 1024)
                            if not chunk:
                                break
                            fh.write(chunk)
                            cur_done += len(chunk)
                            session_bytes += len(chunk)
                            now = time.monotonic()
                            if progress_cb is not None and (now - last_emit >= 0.15 or cur_done >= exp_size):
                                elapsed = max(0.05, now - t_start)
                                speed = float(session_bytes) / elapsed
                                progress_cb(
                                    model_id,
                                    rel,
                                    prior_bytes + cur_done,
                                    model_total_bytes,
                                    speed,
                                    f"Downloading {rel}",
                                )
                                last_emit = now

                if not part_path.is_file() or part_path.stat().st_size != exp_size:
                    got = part_path.stat().st_size if part_path.is_file() else 0
                    part_path.unlink(missing_ok=True)
                    raise ValueError(f"Incomplete download for {rel}: got {got} bytes, expected {exp_size}")

                if progress_cb is not None:
                    progress_cb(
                        model_id,
                        rel,
                        prior_bytes + exp_size,
                        model_total_bytes,
                        0.0,
                        f"Verifying SHA-256 for {rel}",
                    )
                if exp_sha:
                    digest = compute_file_sha256(part_path, cancel_event=cancel_event)
                    if not hmac.compare_digest(digest, exp_sha):
                        part_path.unlink(missing_ok=True)
                        raise ValueError(f"SHA-256 verification failed for {rel}: {digest} != {exp_sha}")

                os.replace(part_path, dest_path)
                return True, "verified"
            except InterruptedError:
                return False, "Download cancelled by user"
            except Exception as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                if attempt < max_retries:
                    time.sleep(min(4.0, 0.75 * (2 ** (attempt - 1))))

        return False, f"Failed downloading {rel} after {max_retries} attempts: {last_err}"

    def provision_model(
        self,
        model_id: str,
        *,
        progress_cb: Optional[ProvisionProgressCb] = None,
        cancel_event: Optional[threading.Event] = None,
        verify_existing_hash: bool = True,
    ) -> Tuple[bool, str]:
        if model_id not in self.entries:
            return False, f"Unknown model id: {model_id}"
        spec = self.entries[model_id]
        target_dir = self.get_model_dir(model_id)
        total_bytes = int(spec.get("expected_size_bytes") or sum(int(f["size"]) for f in spec["files"]))

        if cancel_event is not None and cancel_event.is_set():
            return False, "Provisioning cancelled"

        # 1. Reuse existing verified files if already on machine (never redownload GBs needlessly)
        if progress_cb is not None:
            progress_cb(model_id, "", 0, total_bytes, 0.0, f"Checking existing {spec['display_name']}...")
        reused, reuse_msg = self.try_reuse_existing_model(
            model_id, verify_hash=verify_existing_hash, cancel_event=cancel_event
        )
        if reused:
            if progress_cb is not None:
                progress_cb(model_id, "", total_bytes, total_bytes, 0.0, f"Verified {spec['display_name']}")
            return True, reuse_msg

        # 2. Check free disk space for remaining bytes
        self.models_dir.mkdir(parents=True, exist_ok=True)
        existing_bytes = 0
        for fspec in spec["files"]:
            fp = target_dir / str(fspec["rel_path"])
            part = fp.with_name(fp.name + ".part")
            if fp.is_file() and fp.stat().st_size == int(fspec["size"]):
                existing_bytes += fp.stat().st_size
            elif part.is_file():
                existing_bytes += part.stat().st_size
        remaining_mb = max(64.0, (total_bytes - existing_bytes) / (1024.0 * 1024.0) + 128.0)
        usage = shutil.disk_usage(str(self.models_dir))
        free_mb = usage.free / (1024.0 * 1024.0)
        if free_mb < remaining_mb:
            return (
                False,
                f"Insufficient free disk space in {self.models_dir}: {free_mb:.0f} MB free, {remaining_mb:.0f} MB required for {spec['display_name']}.",
            )

        # 3. Download and verify each file cleanly
        prior_bytes = 0
        for fspec in spec["files"]:
            rel = str(fspec["rel_path"])
            dest_fp = target_dir / rel
            ok, msg = self._download_single_file(
                model_id,
                fspec,
                dest_fp,
                prior_bytes=prior_bytes,
                model_total_bytes=total_bytes,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
            )
            if not ok:
                return False, msg
            prior_bytes += int(fspec["size"])

        _ensure_hf_ref_pointer(target_dir, spec)
        final_st = self.inspect_model(model_id, verify_hash=False)
        if not final_st["installed"]:
            return False, f"Post-download check failed for {model_id}: {final_st['missing_files'] + final_st['corrupt_files']}"

        if progress_cb is not None:
            progress_cb(model_id, "", total_bytes, total_bytes, 0.0, f"{spec['display_name']} verified")
        return True, f"{spec['display_name']} downloaded & verified"

    def estimate_provisioning_plan(
        self,
        *,
        ram_total_gb: Optional[float] = None,
        include_ids: Optional[List[str]] = None,
        app_payload_bytes: int = 700_000_000,
        safety_headroom_bytes: int = 1_500_000_000,
    ) -> Dict[str, Any]:
        """
        Calculate exact selected model download totals, already-cached reusable bytes across known
        VOX model roots, net download size, and required vs available disk space before starting.
        """
        ids = list(include_ids) if include_ids is not None else get_required_model_ids(ram_total_gb=ram_total_gb)
        roots = _discover_candidate_model_roots(self.models_dir)

        total_selected_bytes = 0
        already_present_bytes = 0
        per_model: Dict[str, Any] = {}

        for mid in ids:
            spec = self.entries.get(mid)
            if not spec:
                continue
            exp_total = int(spec.get("expected_size_bytes") or sum(int(f["size"]) for f in spec.get("files", [])))
            total_selected_bytes += exp_total

            # Fast size check on primary + reusable roots (matches inspect_model(verify_hash=False))
            best_present = 0
            reused_from = ""
            for root in roots:
                st = self.inspect_model(mid, verify_hash=False, base_dir=root)
                if st["installed"]:
                    best_present = exp_total
                    reused_from = str(root)
                    break
                best_present = max(best_present, int(st.get("size_bytes") or 0))
            already_present_bytes += min(exp_total, best_present)
            per_model[mid] = {
                "display_name": spec.get("display_name", mid),
                "expected_size_bytes": exp_total,
                "present_bytes": min(exp_total, best_present),
                "remaining_bytes": max(0, exp_total - min(exp_total, best_present)),
                "cached": best_present >= exp_total,
                "reused_from": reused_from,
            }

        download_needed_bytes = max(0, total_selected_bytes - already_present_bytes)
        required_free_disk_bytes = download_needed_bytes + max(0, int(app_payload_bytes)) + max(0, int(safety_headroom_bytes))

        free_disk_bytes = 0
        try:
            probe = self.models_dir
            while not probe.exists() and probe.parent != probe:
                probe = probe.parent
            free_disk_bytes = int(shutil.disk_usage(str(probe)).free)
        except Exception:
            free_disk_bytes = -1

        sufficient = True if free_disk_bytes < 0 else (free_disk_bytes >= required_free_disk_bytes)
        return {
            "selected_model_ids": ids,
            "total_selected_bytes": total_selected_bytes,
            "total_selected_gb_decimal": round(total_selected_bytes / 1_000_000_000.0, 2),
            "already_present_bytes": already_present_bytes,
            "download_needed_bytes": download_needed_bytes,
            "download_needed_gb_decimal": round(download_needed_bytes / 1_000_000_000.0, 2),
            "required_free_disk_bytes": required_free_disk_bytes,
            "required_free_disk_gb": round(required_free_disk_bytes / 1_000_000_000.0, 1),
            "free_disk_bytes": free_disk_bytes,
            "free_disk_gb": round(free_disk_bytes / 1_000_000_000.0, 1) if free_disk_bytes >= 0 else -1.0,
            "sufficient_disk_space": sufficient,
            "per_model": per_model,
        }

    def provision_required_models(
        self,
        *,
        ram_total_gb: Optional[float] = None,
        include_ids: Optional[List[str]] = None,
        progress_cb: Optional[ProvisionProgressCb] = None,
        cancel_event: Optional[threading.Event] = None,
        verify_existing_hash: bool = False,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Provision all mandatory models selected for this host profile in canonical order."""
        ids = list(include_ids) if include_ids is not None else get_required_model_ids(ram_total_gb=ram_total_gb)
        plan = self.estimate_provisioning_plan(
            ram_total_gb=ram_total_gb,
            include_ids=ids,
            app_payload_bytes=0,
            safety_headroom_bytes=512_000_000,
        )
        if plan["free_disk_bytes"] >= 0 and not plan["sufficient_disk_space"]:
            need_gb = plan["required_free_disk_gb"]
            have_gb = plan["free_disk_gb"]
            dl_gb = plan["download_needed_gb_decimal"]
            msg = (
                f"Insufficient free disk space in {self.models_dir}: {have_gb:.1f} GB available, "
                f"{need_gb:.1f} GB required ({dl_gb:.2f} GB to download + safety headroom)."
            )
            return False, {"_disk_check": {"ok": False, "message": msg}}

        results: Dict[str, Any] = {}
        all_ok = True
        for mid in ids:
            if cancel_event is not None and cancel_event.is_set():
                results[mid] = {"ok": False, "message": "Cancelled by user"}
                all_ok = False
                break
            ok, msg = self.provision_model(
                mid,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
                verify_existing_hash=verify_existing_hash,
            )
            results[mid] = {"ok": ok, "message": msg}
            if not ok:
                all_ok = False
                break
        return all_ok, results

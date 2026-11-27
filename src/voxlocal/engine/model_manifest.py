"""
VOX Canonical Model Manifest Loader.

Provides a single source of truth (`packaging/model_manifest.json`) shared across:
- Installer GUI & setup bootstrapper
- First-run health check
- MeetingModelManager & diagnostics
- Incremental updater model compatibility checks
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional


def resolve_manifest_path(explicit_path: Optional[Path | str] = None) -> Path:
    """Locate `model_manifest.json` across explicit override, _MEIPASS, app root, and packaging/."""
    candidates: List[Path] = []
    if explicit_path is not None:
        candidates.append(Path(explicit_path))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp = Path(meipass)
        candidates.extend([mp / "model_manifest.json", mp / "packaging" / "model_manifest.json"])
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                exe_dir / "model_manifest.json",
                exe_dir / "_internal" / "model_manifest.json",
                exe_dir / "packaging" / "model_manifest.json",
            ]
        )
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent.parent
    candidates.extend(
        [
            repo_root / "packaging" / "model_manifest.json",
            here.parent / "model_manifest.json",
        ]
    )
    for cand in candidates:
        if cand.is_file():
            return cand
    return candidates[-2]


@lru_cache(maxsize=4)
def load_model_manifest(manifest_path: Optional[str] = None) -> Dict[str, Any]:
    """Load and parse the canonical model manifest JSON."""
    path = resolve_manifest_path(manifest_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        raise ValueError(f"Invalid model manifest at {path}")
    return data


def get_model_entries(manifest_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Return mapping of `model_id -> model_spec` from the canonical manifest."""
    data = load_model_manifest(manifest_path)
    return {str(m["id"]): dict(m) for m in data.get("models", []) if m.get("id")}


def get_model_spec(model_id: str, manifest_path: Optional[str] = None) -> Dict[str, Any]:
    entries = get_model_entries(manifest_path)
    if model_id not in entries:
        raise KeyError(f"Unknown model id in canonical manifest: {model_id}")
    return entries[model_id]


def get_required_model_ids(ram_total_gb: Optional[float] = None, manifest_path: Optional[str] = None) -> List[str]:
    """
    Return the exact ordered list of model IDs selected for the host system configuration.

    Always includes all unconditionally required models (the 4 Meeting AI models), and
    selects exactly ONE mutually-exclusive Voice Dictation model based on host RAM:
    - RAM < 12 GB -> `dictation_whisper_small` (lightweight fallback, ~0.49 GB)
    - RAM >= 12 GB (or unknown) -> `dictation_whisper_turbo` (primary, ~1.62 GB)
    """
    entries = get_model_entries(manifest_path)
    required: List[str] = []
    for mid, spec in entries.items():
        if spec.get("required", False) and spec.get("mutually_exclusive_group") != "voice_dictation":
            required.append(mid)

    use_small = ram_total_gb is not None and float(ram_total_gb) < 12.0
    dict_id = "dictation_whisper_small" if use_small else "dictation_whisper_turbo"
    if dict_id in entries and dict_id not in required:
        required.append(dict_id)
    return required


def calculate_model_selection_summary(
    ram_total_gb: Optional[float] = None,
    manifest_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Return authoritative byte/GB totals and disk headroom recommendations for selected models."""
    entries = get_model_entries(manifest_path)
    selected_ids = get_required_model_ids(ram_total_gb=ram_total_gb, manifest_path=manifest_path)

    meeting_bytes = 0
    dictation_bytes = 0
    dictation_id = ""
    dictation_name = ""
    for mid in selected_ids:
        spec = entries[mid]
        exp_b = int(spec.get("expected_size_bytes") or sum(int(f["size"]) for f in spec.get("files", [])))
        if spec.get("mutually_exclusive_group") == "voice_dictation" or mid.startswith("dictation_"):
            dictation_id = mid
            dictation_name = str(spec.get("display_name") or mid)
            dictation_bytes += exp_b
        else:
            meeting_bytes += exp_b

    total_bytes = meeting_bytes + dictation_bytes
    # Include ~672 MB app binaries + 1.5 GB extraction/swap safety margin
    min_free_bytes = total_bytes + 700_000_000 + 1_500_000_000
    return {
        "selected_model_ids": selected_ids,
        "selected_count": len(selected_ids),
        "dictation_model_id": dictation_id,
        "dictation_display_name": dictation_name,
        "meeting_models_bytes": meeting_bytes,
        "dictation_model_bytes": dictation_bytes,
        "total_selected_bytes": total_bytes,
        "total_selected_gb_decimal": round(total_bytes / 1_000_000_000.0, 2),
        "total_selected_gib": round(total_bytes / float(1024**3), 2),
        "recommended_min_free_disk_bytes": min_free_bytes,
        "recommended_min_free_disk_gb": round(min_free_bytes / 1_000_000_000.0, 1),
    }


def check_manifest_compatibility(
    app_version: str,
    manifest_data: Optional[Dict[str, Any]] = None,
) -> bool:
    """Verify whether the current application version satisfies `min_app_version`."""
    data = manifest_data or load_model_manifest()
    min_ver = str(data.get("min_app_version") or "2.0.0").strip()
    try:
        from voxlocal.core.updater import parse_version_tuple

        return parse_version_tuple(app_version) >= parse_version_tuple(min_ver)
    except Exception:
        return True

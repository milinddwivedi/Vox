"""
VOX Meeting AI Model & Runtime Manager (100% Local & Separate from Voice Dictation).

Manages the 4 local Meeting AI artifacts and the isolated llama.cpp runtime:
1. meeting_asr         -> Cohere Transcribe 2B (ONNX INT8, Apache 2.0, CPU)
2. meeting_diarization -> Sherpa Neural Diarization (pyannote-seg-3.0 + WeSpeaker ResNet34-LM)
3. meeting_llm         -> Qwen3.5-4B-Q4_K_M GGUF (Apache 2.0, Vulkan GPU + CPU fallback)
4. meeting_embeddings  -> BGE-Small-EN v1.5 ONNX INT8 (MIT, 384-d CPU embeddings)

Voice Dictation Whisper models are intentionally excluded and untouched.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from voxlocal.core.logger import check_disk_space_mb, get_app_root, get_logger, get_user_models_root

logger = get_logger("engine.meeting_model_manager")


MEETING_ARTIFACTS: Dict[str, Dict[str, Any]] = {
    "meeting_asr": {
        "key": "meeting_asr",
        "display_name": "Cohere Transcribe 2B (English Post-Call ASR)",
        "role": "Primary English post-call transcription (Indian & US English)",
        "license": "Apache-2.0",
        "version": "cohere-transcribe-03-2026-int8",
        "expected_size_mb": 2754,
        "min_disk_space_mb": 3400,
        "subdir": ("meeting_asr", "cohere-transcribe-en-int8"),
        "required_files": {
            "encoder.int8.onnx": 100_000,
            "encoder.int8.onnx.data": 500_000_000,
            "decoder.int8.onnx": 100_000_000,
            "tokens.txt": 1_000,
        },
        "hf_repo_id": "csukuangfj2/sherpa-onnx-cohere-transcribe-14-lang-int8-2026-04-01",
        "hf_allow_patterns": [
            "encoder.int8.onnx",
            "encoder.int8.onnx.data",
            "decoder.int8.onnx",
            "tokens.txt",
            "README.md",
        ],
    },
    "meeting_diarization": {
        "key": "meeting_diarization",
        "display_name": "Sherpa Neural Diarization (PyAnnote + WeSpeaker)",
        "role": "Local neural speaker segmentation & global speaker clustering",
        "license": "MIT / Apache-2.0",
        "version": "pyannote-3.0+wespeaker-resnet34-lm",
        "expected_size_mb": 32,
        "min_disk_space_mb": 120,
        "subdir": ("meeting_diarization", "sherpa-diarization"),
        "required_files": {
            "pyannote-seg-3-0/model.onnx": 1_000_000,
            "wespeaker_en_voxceleb_resnet34_LM.onnx": 10_000_000,
        },
        "hf_repo_id": "csukuangfj/sherpa-onnx-pyannote-segmentation-3-0",
        "hf_allow_patterns": ["model.onnx"],
    },
    "meeting_llm": {
        "key": "meeting_llm",
        "display_name": "Qwen3.5-4B (Q4_K_M GGUF)",
        "role": "Local Meeting Intelligence, Summaries, Decisions, Actions, Knowledge & Ask VOX",
        "license": "Apache-2.0",
        "version": "Qwen3.5-4B-Q4_K_M",
        "expected_size_mb": 2614,
        "min_disk_space_mb": 3200,
        "subdir": ("meeting_llm",),
        "required_files": {
            "Qwen3.5-4B-Q4_K_M.gguf": 2_000_000_000,
        },
        "hf_repo_id": "unsloth/Qwen3.5-4B-GGUF",
        "hf_allow_patterns": ["Qwen3.5-4B-Q4_K_M.gguf"],
    },
    "meeting_embeddings": {
        "key": "meeting_embeddings",
        "display_name": "BGE-Small-EN v1.5 (ONNX INT8)",
        "role": "384-d semantic vectors for local RAG & Cross-Meeting Knowledge search",
        "license": "MIT",
        "version": "bge-small-en-v1.5-onnx-Q",
        "expected_size_mb": 34,
        "min_disk_space_mb": 120,
        "subdir": ("meeting_embeddings", "bge-small-en-v1.5-onnx-Q"),
        "required_files": {
            "model_optimized.onnx": 20_000_000,
            "tokenizer.json": 100_000,
        },
        "hf_repo_id": "Qdrant/bge-small-en-v1.5-onnx-Q",
        "hf_allow_patterns": [
            "model_optimized.onnx",
            "tokenizer.json",
            "config.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
        ],
    },
}


def resolve_llama_server_bin(
    prefer_gpu: bool = True,
    app_root: Optional[Path] = None,
) -> Tuple[Optional[Path], str]:
    """
    Locate bundled llama-server.exe across dev workspace, PyInstaller _MEIPASS, and data/runtime.
    Returns (path_or_none, variant_name) where variant_name in ('vulkan', 'cpu', 'missing').
    """
    roots: List[Path] = []
    if app_root is not None:
        roots.append(Path(app_root))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    roots.append(get_app_root())

    order = ("vulkan", "cpu") if prefer_gpu else ("cpu",)
    for variant in order:
        for base in roots:
            for rel in (
                base / "runtime" / "llama_cpp" / variant / "llama-server.exe",
                base / "data" / "runtime" / "llama_cpp" / variant / "llama-server.exe",
            ):
                if (
                    rel.is_file()
                    and rel.stat().st_size > 4_000
                    and (rel.parent / "llama.dll").is_file()
                ):
                    return rel, variant
    return None, "missing"


class MeetingModelManager:
    """Inspect, verify, and download local Meeting AI artifacts cleanly on demand."""

    def __init__(self, models_dir: Optional[str | Path] = None) -> None:
        if models_dir is None:
            self.models_dir = get_user_models_root()
        else:
            self.models_dir = Path(models_dir)
        self._lock = threading.RLock()

    def get_artifact_dir(self, key: str) -> Path:
        spec = MEETING_ARTIFACTS[key]
        p = self.models_dir
        for part in spec["subdir"]:
            p = p / part
        return p

    def get_artifact_status(self, key: str) -> Dict[str, Any]:
        if key not in MEETING_ARTIFACTS:
            raise KeyError(f"Unknown meeting artifact: {key}")
        spec = MEETING_ARTIFACTS[key]
        target_dir = self.get_artifact_dir(key)
        missing: List[str] = []
        size_bytes = 0
        for rel_file, min_bytes in spec["required_files"].items():
            fp = target_dir / rel_file
            if not fp.is_file():
                missing.append(rel_file)
            else:
                sz = fp.stat().st_size
                size_bytes += sz
                if sz < int(min_bytes):
                    missing.append(f"{rel_file} (corrupt/incomplete: {sz} bytes)")

        if target_dir.is_dir() and not missing:
            try:
                size_bytes = sum(f.stat().st_size for f in target_dir.rglob("*") if f.is_file())
            except OSError:
                pass

        installed = len(missing) == 0
        out: Dict[str, Any] = {
            "key": key,
            "display_name": spec["display_name"],
            "role": spec["role"],
            "license": spec["license"],
            "version": spec["version"],
            "path": str(target_dir),
            "installed": installed,
            "verified": installed,
            "missing_files": missing,
            "size_mb": round(size_bytes / (1024 * 1024), 1),
            "expected_size_mb": spec["expected_size_mb"],
            "downloadable": bool(spec.get("hf_repo_id")),
        }
        if key == "meeting_llm":
            srv_gpu, _ = resolve_llama_server_bin(prefer_gpu=True)
            srv_cpu, _ = resolve_llama_server_bin(prefer_gpu=False)
            out["runtime_vulkan_ready"] = srv_gpu is not None
            out["runtime_cpu_ready"] = srv_cpu is not None
        return out

    def get_all_statuses(self) -> Dict[str, Dict[str, Any]]:
        return {k: self.get_artifact_status(k) for k in MEETING_ARTIFACTS}

    def verify_artifact(self, key: str) -> Tuple[bool, str]:
        st = self.get_artifact_status(key)
        if not st["installed"]:
            return False, f"Missing or incomplete files: {', '.join(st['missing_files'])}"
        return True, f"{st['display_name']} verified ({st['size_mb']:.1f} MB)"

    def download_artifact(
        self,
        key: str,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> Tuple[bool, str]:
        """Download and verify a Meeting AI artifact onto the configured models drive."""
        if key not in MEETING_ARTIFACTS:
            return False, f"Unknown meeting model key: {key}"
        spec = MEETING_ARTIFACTS[key]
        target_dir = self.get_artifact_dir(key)

        if self.get_artifact_status(key)["installed"]:
            if progress_callback:
                progress_callback(100, f"{spec['display_name']} already installed & verified")
            return True, f"{spec['display_name']} already installed"

        repo_id = spec.get("hf_repo_id")
        if not repo_id:
            return False, f"{spec['display_name']} has no automatic HF repo configured"

        os.makedirs(self.models_dir, exist_ok=True)
        ok_disk, free_mb, disk_msg = check_disk_space_mb(
            self.models_dir,
            min_free_mb=float(spec.get("min_disk_space_mb", 500)),
        )
        if not ok_disk:
            return False, disk_msg

        # Redirect HF caches onto the models drive (never fill C:)
        hf_cache_dir = self.models_dir / ".hf_cache"
        os.environ["HF_HOME"] = str(hf_cache_dir)

        with self._lock:
            staging_dir = self.models_dir / f".staging_{key}"
            try:
                if progress_callback:
                    progress_callback(5, f"Downloading {spec['display_name']}...")
                from voxlocal.engine.model_provisioner import ModelProvisioner

                prov = ModelProvisioner(models_dir=self.models_dir)

                def _on_prov(mid: str, rel: str, done_b: int, tot_b: int, _spd: float, stg: str) -> None:
                    if progress_callback and tot_b > 0:
                        pct = int(max(5, min(99, (done_b * 100) // tot_b)))
                        progress_callback(pct, stg)

                ok_prov, msg_prov = prov.provision_model(key, progress_cb=_on_prov, verify_existing_hash=False)
                if ok_prov:
                    if progress_callback:
                        progress_callback(100, f"{spec['display_name']} ready")
                    return True, msg_prov

                from huggingface_hub import snapshot_download

                staging_dir.mkdir(parents=True, exist_ok=True)
                snapshot_download(
                    repo_id=repo_id,
                    local_dir=str(staging_dir),
                    allow_patterns=spec.get("hf_allow_patterns") or None,
                    cache_dir=str(hf_cache_dir),
                )
                target_dir.mkdir(parents=True, exist_ok=True)
                for item in staging_dir.rglob("*"):
                    if item.is_file() and ".cache" not in item.parts:
                        rel = item.relative_to(staging_dir)
                        dst = target_dir / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(item), str(dst))

                ok, msg = self.verify_artifact(key)
                if ok and progress_callback:
                    progress_callback(100, f"{spec['display_name']} ready")
                return ok, msg
            except Exception as exc:
                logger.error("Failed downloading %s: %s", key, exc, exc_info=True)
                return False, f"Download failed for {spec['display_name']}: {exc}"
            finally:
                shutil.rmtree(staging_dir, ignore_errors=True)

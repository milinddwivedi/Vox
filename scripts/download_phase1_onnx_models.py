"""
Download Phase 1 ONNX INT8 ASR Challenger Models into `data/models/challengers/`.

Models:
1. csukuangfj2/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25
2. csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8
3. csukuangfj2/sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25

Never modifies `src/voxlocal/` or `data/voxlocal.db`.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Dict, List

from huggingface_hub import snapshot_download

ROOT_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT_DIR / "data" / "models" / "challengers"

MODELS = [
    {
        "id": "onnx_qwen3_asr_06b_int8",
        "repo_id": "csukuangfj2/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25",
        "local_dir": MODELS_DIR / "sherpa-onnx-qwen3-asr-0.6b-int8",
        "allow_patterns": ["*.onnx", "tokenizer/*", "README.md", "*.txt"],
        "required_files": [
            "conv_frontend.onnx",
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "tokenizer/vocab.json",
            "tokenizer/merges.txt",
            "tokenizer/tokenizer_config.json",
        ],
    },
    {
        "id": "onnx_parakeet_tdt_v2_int8",
        "repo_id": "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
        "local_dir": MODELS_DIR / "sherpa-onnx-parakeet-tdt-0.6b-v2-int8",
        "allow_patterns": ["*.onnx", "*.txt", "*.json", "README.md"],
        "required_files": [
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "joiner.int8.onnx",
            "tokens.txt",
        ],
    },
    {
        "id": "onnx_nemotron_stream_560ms_int8",
        "repo_id": "csukuangfj2/sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25",
        "local_dir": MODELS_DIR / "sherpa-onnx-nemotron-streaming-en-0.6b-560ms-int8",
        "allow_patterns": ["*.onnx", "*.txt", "*.json", "README.md"],
        "required_files": [
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "joiner.int8.onnx",
            "tokens.txt",
        ],
    },
]


def download_all() -> Dict[str, Dict[str, object]]:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Dict[str, object]] = {}
    for spec in MODELS:
        local_dir: Path = spec["local_dir"]
        local_dir.mkdir(parents=True, exist_ok=True)
        already = all((local_dir / rel).exists() for rel in spec["required_files"])
        t0 = time.perf_counter()
        if not already:
            print(f"[Download] {spec['id']} <- {spec['repo_id']} ...")
            snapshot_download(
                repo_id=spec["repo_id"],
                local_dir=str(local_dir),
                allow_patterns=spec["allow_patterns"],
                ignore_patterns=["test_wavs/*", "*.wav", "*.flac"],
            )
        else:
            print(f"[Cached] {spec['id']} already present at {local_dir}")

        elapsed = round(time.perf_counter() - t0, 2)
        files_info: Dict[str, float] = {}
        missing: List[str] = []
        for rel in spec["required_files"]:
            fp = local_dir / rel
            if fp.exists():
                files_info[rel] = round(fp.stat().st_size / (1024.0 * 1024.0), 2)
            else:
                missing.append(rel)
        if missing:
            raise FileNotFoundError(f"Missing files for {spec['id']}: {missing}")
        total_mb = round(sum(fp.stat().st_size for fp in local_dir.rglob("*") if fp.is_file()) / (1024.0 * 1024.0), 2)
        report[spec["id"]] = {
            "repo_id": spec["repo_id"],
            "local_dir": str(local_dir),
            "elapsed_sec": elapsed,
            "total_disk_mb": total_mb,
            "files_mb": files_info,
        }
        print(f"  [OK] {spec['id']}: {total_mb:.1f} MB ({elapsed:.2f}s)")
    return report


def main() -> int:
    rep = download_all()
    out = ROOT_DIR / "data" / "benchmarks" / "phase1_onnx_models_manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Portable Hardware & CPU/GPU ASR Tier Diagnostics CLI for VOX.

Run on any target Windows laptop (Dell, Lenovo, HP, ASUS, desktop workstation)
to capture exact hardware capabilities, free memory/VRAM, selected ExecutionProfile,
and an optional short synthetic CPU benchmark without modifying user data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.core.hardware import HardwareProfiler


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect VOX portable hardware profile and adaptive ASR execution diagnostics."
    )
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="Optional JSON file path to save diagnostics.",
    )
    parser.add_argument(
        "--short-cpu-benchmark",
        action="store_true",
        help="Run a short ~3s synthetic CPU int8 Whisper throughput check using cached model if available.",
    )
    args = parser.parse_args(argv)

    cfg = AppConfig.load()
    report = HardwareProfiler.run_hardware_diagnostics(
        run_short_benchmark=bool(args.short_cpu_benchmark),
        config=cfg,
    )
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

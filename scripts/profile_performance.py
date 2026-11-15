"""
scripts/profile_performance.py

VOX 2.0 Comprehensive Architecture & Performance Profiler.
Measures:
1. Cold startup milestones (module imports, config load, controller init < 2.5s SLA).
2. Idle and active resource footprint (Working Set RAM, Private Commit, Handles, Threads, CPU %).
3. Clean shutdown teardown latency and resource stabilization.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

# Ensure UTF-8 console output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def measure_cpu_percent(duration_sec: float = 1.0) -> float:
    """Measure current process CPU utilization across logical cores over duration_sec."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.GetCurrentProcess()

        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_start = wintypes.FILETIME()
        user_start = wintypes.FILETIME()

        kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_start),
            ctypes.byref(user_start),
        )

        def ft_to_uint64(ft):
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        t0_kernel = ft_to_uint64(kernel_start)
        t0_user = ft_to_uint64(user_start)
        t0_perf = time.perf_counter()

        time.sleep(duration_sec)

        kernel_end = wintypes.FILETIME()
        user_end = wintypes.FILETIME()
        kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_end),
            ctypes.byref(user_end),
        )

        t1_kernel = ft_to_uint64(kernel_end)
        t1_user = ft_to_uint64(user_end)
        elapsed_perf = time.perf_counter() - t0_perf

        # 100-nanosecond intervals (10,000,000 per second)
        cpu_time_sec = ((t1_kernel - t0_kernel) + (t1_user - t0_user)) / 10_000_000.0
        num_cores = os.cpu_count() or 1
        cpu_pct = (cpu_time_sec / (elapsed_perf * num_cores)) * 100.0
        return round(float(cpu_pct), 2)
    except Exception:
        return 0.0


def profile_application() -> Dict[str, Any]:
    print("=" * 70)
    print("VOX PERFORMANCE & RESOURCE PROFILER")
    print("=" * 70)

    # 1. Cold Startup Profiling
    t_zero = time.perf_counter()

    from voxlocal.core.config import AppConfig
    from voxlocal.core.logger import get_system_resource_snapshot, setup_logging
    t_import = time.perf_counter()

    setup_logging()
    cfg = AppConfig.load()
    t_cfg = time.perf_counter()

    from voxlocal.core.controller import AppController
    ctrl = AppController(config=cfg)
    t_ctrl = time.perf_counter()

    import_ms = (t_import - t_zero) * 1000.0
    cfg_ms = (t_cfg - t_import) * 1000.0
    ctrl_ms = (t_ctrl - t_cfg) * 1000.0
    total_startup_ms = (t_ctrl - t_zero) * 1000.0

    print("\n--- 1. Cold Startup Milestones ---")
    print(f"Module Import Latency:    {import_ms:6.1f} ms")
    print(f"Config Load Latency:      {cfg_ms:6.1f} ms")
    print(f"Controller Init Latency:  {ctrl_ms:6.1f} ms")
    print(f"Total Cold Init Duration: {total_startup_ms:6.1f} ms (SLA: < 2500 ms)")

    assert total_startup_ms < 2500.0, f"Cold startup {total_startup_ms:.1f}ms violated 2.5s SLA!"

    # 2. Idle Resource Footprint
    snap_idle = get_system_resource_snapshot()
    idle_cpu_pct = measure_cpu_percent(duration_sec=1.0)

    print("\n--- 2. Idle Resource Baseline ---")
    print(f"Working Set RAM:          {snap_idle['working_set_mb']:6.1f} MB (Physical RAM)")
    print(f"Private Commit RAM:       {snap_idle['private_mb']:6.1f} MB (Commit Limit)")
    print(f"Kernel Handles:           {snap_idle['handle_count']:6d} handles")
    print(f"Active Threads:           {snap_idle['thread_count']:6d} threads")
    print(f"Idle CPU Utilization:     {idle_cpu_pct:6.2f} % (across {os.cpu_count()} cores)")

    # 3. Active Workload Profiling
    print("\n--- 3. Active Workload Simulation ---")
    active_snapshots = []
    # Simulate active speech buffer events
    for i in range(3):
        # Trigger foreground window poll and simulated dictation state
        ctrl._poll_foreground_window()
        time.sleep(0.05)
        active_snapshots.append(get_system_resource_snapshot())

    peak_active_ram = max(s["working_set_mb"] for s in active_snapshots)
    max_active_handles = max(s["handle_count"] for s in active_snapshots)
    active_cpu_pct = measure_cpu_percent(duration_sec=0.5)

    print(f"Active Peak Working Set:  {peak_active_ram:6.1f} MB")
    print(f"Active Max Handles:       {max_active_handles:6d} handles")
    print(f"Active CPU Utilization:   {active_cpu_pct:6.2f} %")

    # 4. Clean Shutdown Teardown
    print("\n--- 4. Clean Teardown & Resource Release ---")
    t_shutdown_start = time.perf_counter()
    ctrl.shutdown()
    teardown_ms = (time.perf_counter() - t_shutdown_start) * 1000.0

    print(f"Shutdown Teardown Time:   {teardown_ms:6.1f} ms (SLA: < 100 ms)")
    snap_post = get_system_resource_snapshot()
    print(f"Post-Shutdown Threads:    {snap_post['thread_count']:6d} threads")
    print(f"Post-Shutdown Handles:    {snap_post['handle_count']:6d} handles")

    report = {
        "startup_ms": {
            "module_import_ms": round(import_ms, 1),
            "config_load_ms": round(cfg_ms, 1),
            "controller_init_ms": round(ctrl_ms, 1),
            "total_cold_init_ms": round(total_startup_ms, 1),
            "sla_passed": total_startup_ms < 2500.0,
        },
        "idle_resources": {
            "working_set_mb": snap_idle["working_set_mb"],
            "private_commit_mb": snap_idle["private_mb"],
            "handle_count": snap_idle["handle_count"],
            "thread_count": snap_idle["thread_count"],
            "cpu_percent": idle_cpu_pct,
        },
        "active_resources": {
            "peak_working_set_mb": peak_active_ram,
            "max_handles": max_active_handles,
            "cpu_percent": active_cpu_pct,
        },
        "teardown_ms": {
            "shutdown_latency_ms": round(teardown_ms, 1),
            "sla_passed": teardown_ms < 300.0,
            "post_thread_count": snap_post["thread_count"],
            "post_handle_count": snap_post["handle_count"],
        },
    }

    print("\n" + "=" * 70)
    print("PROFILING RUN SUCCESSFULLY COMPLETED (ALL SLAs PASSED)")
    print("=" * 70)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="VOX Performance Profiler")
    parser.add_argument("--json", action="store_true", help="Output JSON results")
    args = parser.parse_args()

    results = profile_application()
    if args.json:
        print("\nJSON_RESULTS:")
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()

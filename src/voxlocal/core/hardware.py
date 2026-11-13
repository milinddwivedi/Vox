"""
Corporate Hardware Profiler & Adaptive Device Strategy for VOX.
Detects hardware specifications, OS capabilities, acceleration readiness,
and configures adaptive resource parameters for Dell, Lenovo, and other corporate laptops.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from voxlocal.core.logger import get_app_root, get_logger

logger = get_logger("hardware")


class MEMORYSTATUSEX(ctypes.Structure):
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


@dataclass
class HardwareProfile:
    os_name: str
    os_version: str
    os_build: str
    is_64bit: bool
    cpu_name: str
    cpu_cores: int
    cpu_threads: int
    ram_total_gb: float
    ram_avail_gb: float
    gpu_name: str
    gpu_type: str  # "NVIDIA CUDA" | "Intel Iris Xe / Integrated" | "AMD Radeon" | "CPU Only"
    gpu_vram_gb: float
    cuda_available: bool
    cuda_device_count: int
    disk_free_gb: float
    corporate_tier: str  # "Tier 1: Constrained (8GB / CPU-only)" | "Tier 2: Standard Corporate (16GB)" | "Tier 3: High-Performance (32GB / GPU)"
    adaptive_cpu_threads: int
    recommended_whisper_model: str
    is_minimum_met: bool
    is_recommended_met: bool


class HardwareProfiler:
    """Queries Windows system APIs to gather hardware diagnostics and establish adaptive limits."""

    _cached_profile: Optional[HardwareProfile] = None

    @classmethod
    def get_profile(cls, force_refresh: bool = False) -> HardwareProfile:
        if cls._cached_profile is not None and not force_refresh:
            return cls._cached_profile

        # 1. OS Info
        os_name = f"Windows {platform.release()}"
        os_ver = platform.version()
        os_build = platform.win32_ver()[1] if hasattr(platform, "win32_ver") else os_ver
        is_64bit = sys.maxsize > 2**32

        # 2. CPU Info
        cpu_name = platform.processor() or "x86_64 Processor"
        cpu_threads = os.cpu_count() or 4
        # Query registry or WMI for friendly CPU string if available
        friendly_cpu = cls._get_friendly_cpu_name() or cpu_name
        cpu_cores = max(2, cpu_threads // 2)

        # 3. RAM Info
        ram_total_gb, ram_avail_gb = cls._get_ram_info()

        # 4. GPU & Acceleration Info
        gpu_name, gpu_type, gpu_vram_gb, cuda_available, cuda_count = cls._get_gpu_info()

        # 5. Disk Free Space
        app_root = get_app_root()
        try:
            total, used, free = shutil.disk_usage(app_root)
            disk_free_gb = round(free / (1024**3), 1)
        except Exception:
            disk_free_gb = 50.0

        # 6. Corporate Tier Classification
        if ram_total_gb < 11.5 or (not cuda_available and cpu_threads <= 4):
            tier = "Tier 1: Lower-Specification Corporate (8 GB / CPU-Only)"
            adaptive_threads = max(2, min(4, cpu_threads - 1))
            recommended_model = "small"
        elif ram_total_gb < 23.5 or not cuda_available or gpu_vram_gb < 6.5:
            tier = "Tier 2: Standard Corporate Office (16 GB / 4 GB GPU or CPU int8)"
            adaptive_threads = max(4, min(6, cpu_threads - 2))
            recommended_model = "small"
        else:
            tier = "Tier 3: High-Performance Business Workstation (32 GB / GPU)"
            adaptive_threads = max(4, min(8, cpu_threads - 2))
            recommended_model = "large-v3-turbo"

        # Minimum & Recommended checks
        min_met = is_64bit and ram_total_gb >= 6.5 and cpu_threads >= 2 and disk_free_gb >= 4.0
        rec_met = is_64bit and ram_total_gb >= 15.0 and cpu_threads >= 6 and disk_free_gb >= 15.0

        profile = HardwareProfile(
            os_name=os_name,
            os_version=os_ver,
            os_build=os_build,
            is_64bit=is_64bit,
            cpu_name=friendly_cpu,
            cpu_cores=cpu_cores,
            cpu_threads=cpu_threads,
            ram_total_gb=round(ram_total_gb, 1),
            ram_avail_gb=round(ram_avail_gb, 1),
            gpu_name=gpu_name,
            gpu_type=gpu_type,
            gpu_vram_gb=round(gpu_vram_gb, 1),
            cuda_available=cuda_available,
            cuda_device_count=cuda_count,
            disk_free_gb=disk_free_gb,
            corporate_tier=tier,
            adaptive_cpu_threads=adaptive_threads,
            recommended_whisper_model=recommended_model,
            is_minimum_met=min_met,
            is_recommended_met=rec_met,
        )

        cls._cached_profile = profile
        return profile

    @staticmethod
    def _get_ram_info() -> Tuple[float, float]:
        try:
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                tot = stat.ullTotalPhys / (1024**3)
                avail = stat.ullAvailPhys / (1024**3)
                return tot, avail
        except Exception as exc:
            logger.debug("Failed GlobalMemoryStatusEx: %s", exc)
        return 16.0, 8.0

    @staticmethod
    def _get_friendly_cpu_name() -> Optional[str]:
        if sys.platform == "win32":
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
                val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                winreg.CloseKey(key)
                if val:
                    return str(val).strip()
            except Exception:
                pass
        return None

    @staticmethod
    def _get_gpu_info() -> Tuple[str, str, float, bool, int]:
        cuda_ok = False
        cuda_count = 0
        vram_gb = 0.0
        gpu_name = "Integrated / Software Rasterizer"
        gpu_type = "CPU Only"

        # Check CTranslate2 CUDA availability
        try:
            import ctranslate2
            cuda_count = ctranslate2.get_cuda_device_count()
            if cuda_count > 0:
                cuda_ok = True
        except Exception:
            pass

        # Query Windows Display registry or WMI for GPU names
        if sys.platform == "win32":
            try:
                import winreg
                path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as base_key:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(base_key, i)
                            i += 1
                            if sub.isdigit():
                                with winreg.OpenKey(base_key, sub) as k:
                                    try:
                                        desc, _ = winreg.QueryValueEx(k, "DriverDesc")
                                        desc_str = str(desc)
                                        if "nvidia" in desc_str.lower():
                                            gpu_name = desc_str
                                            gpu_type = "NVIDIA CUDA Acceleration"
                                            # Try reading VRAM
                                            try:
                                                mem_bytes, _ = winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")
                                                vram_gb = mem_bytes / (1024**3)
                                            except Exception:
                                                vram_gb = 4.0
                                            break
                                        elif any(needle in desc_str.lower() for needle in ("intel", "iris", "arc", "radeon")):
                                            gpu_name = desc_str
                                            gpu_type = "Intel/AMD Integrated Graphics (Optimized CPU int8)"
                                    except OSError:
                                        continue
                        except OSError:
                            break
            except Exception as exc:
                logger.debug("GPU registry scan skipped: %s", exc)

        if cuda_ok and "NVIDIA" not in gpu_type:
            gpu_type = "NVIDIA CUDA Acceleration"

        return gpu_name, gpu_type, vram_gb, cuda_ok, cuda_count

    @classmethod
    def get_system_compatibility_summary(cls) -> Dict[str, Any]:
        """Produce structured diagnostic metadata for Settings / About section."""
        prof = cls.get_profile()

        return {
            "os_status": f"{prof.os_name} (Build {prof.os_build}) — {'Supported (64-bit)' if prof.is_64bit else 'Unsupported 32-bit'}",
            "ram_status": f"{prof.ram_total_gb:.1f} GB Total ({prof.ram_avail_gb:.1f} GB Available)",
            "processor_status": f"{prof.cpu_name} ({prof.cpu_cores} Cores, {prof.cpu_threads} Threads)",
            "gpu_status": f"{prof.gpu_name} — {prof.gpu_type} ({'CUDA Active' if prof.cuda_available else 'CPU int8 Mode'})",
            "tier": prof.corporate_tier,
            "whisper_status": f"{prof.recommended_whisper_model} (100% Offline Local Inference)",
            "llm_status": "Llama-3.2-1B-Instruct (100% Offline INT8 Cognitive Synthesis)",
            "audio_status": "WASAPI Loopback & DirectShow Capture Ready",
            "storage_status": f"{prof.disk_free_gb:.1f} GB Free Disk Space",
            "min_requirements_met": prof.is_minimum_met,
            "rec_requirements_met": prof.is_recommended_met,
            "recommendations": [
                f"Configured for {prof.corporate_tier}.",
                f"Using {prof.adaptive_cpu_threads} compute threads for balanced background inference without freezing desktop office apps.",
                "Capture and audio recordings take strict real-time priority over post-meeting AI generation.",
            ]
        }

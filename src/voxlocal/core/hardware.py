"""
Corporate Hardware Profiler & Adaptive Device Strategy for VOX.
Detects hardware specifications, OS capabilities, acceleration readiness,
and configures adaptive resource parameters for Dell, Lenovo, and other corporate laptops.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from voxlocal.core.logger import get_app_root, get_logger

logger = get_logger("hardware")


@dataclass
class ExecutionProfile:
    """Adaptive live + offline ASR execution plan selected from host CPU, RAM, and GPU headroom."""

    tier_id: str
    tier_label: str
    live_backend: str
    live_model: str
    live_device: str
    live_compute_type: str
    offline_backend: str
    offline_model: str
    offline_device: str
    offline_compute_type: str
    safe_cpu_threads: int
    live_best_effort: bool
    max_concurrent_models: int
    hf_runtime_available: bool
    free_vram_mb: Optional[int]
    fallback_reason: str = ""
    session_mode: str = "auto"
    live_execution_mode: str = "endpointed"
    selective_verifier_backend: str = "disabled"
    phase2_enabled: bool = False
    available_phase2_models: Dict[str, bool] = field(default_factory=dict)
    post_call_verifier_backend: str = "disabled"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


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

        # Check CTranslate2 CUDA availability AND verify cuBLAS runtime DLL is loadable on Windows
        try:
            import ctranslate2
            from voxlocal.engine.transcriber import is_cuda_runtime_available
            cuda_count = ctranslate2.get_cuda_device_count()
            if cuda_count > 0 and is_cuda_runtime_available():
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

    @staticmethod
    def _query_live_gpu_vram_mb() -> Tuple[Optional[int], Optional[int]]:
        """Query current `(used_mb, free_mb)` via nvidia-smi without initializing CUDA context."""
        try:
            kw: Dict[str, Any] = dict(stderr=subprocess.DEVNULL, text=True, timeout=2.0)
            if os.name == "nt":
                kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                **kw,
            ).strip()
            if out:
                parts = [p.strip() for p in out.splitlines()[0].split(",")]
                if len(parts) >= 2:
                    return int(parts[0]), int(parts[1])
        except Exception:
            pass
        return None, None

    @staticmethod
    def _check_hf_asr_runtime_available() -> bool:
        """Return True iff torch and transformers can be found without importing either eagerly."""
        try:
            return (
                importlib.util.find_spec("torch") is not None
                and importlib.util.find_spec("transformers") is not None
            )
        except Exception:
            return False

    @staticmethod
    def _has_local_hf_asr_checkpoint(model_id: str, models_dir: Optional[Path] = None) -> bool:
        """Check cleanly if an HF ASR checkpoint (Qwen3/Maya) already exists locally on disk."""
        mid = str(model_id or "").strip()
        if not mid:
            return False
        p_direct = Path(mid)
        if p_direct.is_dir() and (p_direct / "config.json").exists():
            return True

        base_dirs: List[Path] = []
        if models_dir is not None:
            base_dirs.append(Path(models_dir))
        app_root = get_app_root()
        base_dirs.append(app_root / "data" / "models")

        slugs: List[str] = []
        low = mid.lower()
        if "maya" in low:
            slugs.append("maya-transcribe")
        elif "hinglish" in low or "moorlee" in low:
            slugs.append("qwen3-asr-0.6b-hinglish")
        elif "qwen3-asr" in low:
            slugs.append("qwen3-asr-0.6b")
        slugs.append(mid.replace("/", "--"))

        for bd in base_dirs:
            for slug in slugs:
                for cand in (bd / "challengers" / slug, bd / "hf_asr" / slug, bd / slug):
                    if (cand / "config.json").exists() and (
                        (cand / "model.safetensors").exists() or list(cand.glob("*.safetensors"))
                    ):
                        return True
            hub_dir = bd / f"models--{mid.replace('/', '--')}" / "snapshots"
            if hub_dir.exists() and any((s / "config.json").exists() for s in hub_dir.iterdir() if s.is_dir()):
                return True

        hf_hub = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{mid.replace('/', '--')}" / "snapshots"
        if hf_hub.exists() and any((s / "config.json").exists() for s in hf_hub.iterdir() if s.is_dir()):
            return True
        return False

    @classmethod
    def select_execution_profile(
        cls,
        config: Any = None,
        force_refresh: bool = False,
    ) -> ExecutionProfile:
        """
        Determine hardware-adaptive live & offline ASR execution settings across Tier 1, Tier 2,
        and Tier 3 devices while never loading two heavy models simultaneously.
        """
        prof = cls.get_profile(force_refresh=force_refresh)
        hf_avail = cls._check_hf_asr_runtime_available()
        used_vram, free_vram = cls._query_live_gpu_vram_mb() if prof.cuda_available else (None, None)
        if prof.cuda_available and free_vram is None and prof.gpu_vram_gb > 0:
            free_vram = int(round(prof.gpu_vram_gb * 1024 * 0.7))

        models_dir_raw = getattr(config, "models_dir", "") if config is not None else ""
        models_dir = Path(models_dir_raw) if models_dir_raw else None
        hf_model_id = str(getattr(config, "hf_asr_model_id", "Qwen/Qwen3-ASR-0.6B-hf") or "Qwen/Qwen3-ASR-0.6B-hf")
        hf_rc_enabled = bool(getattr(config, "enable_hf_asr_rc", False)) if config is not None else False
        live_pref = str(getattr(config, "live_asr_backend", "auto") or "auto").strip().lower()
        off_pref = str(getattr(config, "offline_asr_backend", "auto") or "auto").strip().lower()
        adaptive_enabled = bool(getattr(config, "enable_hardware_adaptive_asr", True)) if config is not None else True

        hf_ckpt_ok = cls._has_local_hf_asr_checkpoint(hf_model_id, models_dir=models_dir) if hf_avail else False
        fallback_reasons: List[str] = []

        # Phase 2 Hardware-Aware Adaptive ONNX / Incremental / Guarded Selector
        phase2_flag = bool(getattr(config, "enable_phase2_asr_pipeline", False)) if config is not None else False
        if phase2_flag and adaptive_enabled:
            phase2_profile = cls._select_phase2_execution_profile(
                prof=prof,
                config=config,
                models_dir=models_dir,
                free_vram=free_vram,
                hf_avail=hf_avail,
                hf_ckpt_ok=hf_ckpt_ok,
                hf_model_id=hf_model_id,
            )
            if phase2_profile is not None:
                return phase2_profile

        # Tier 1: Minimum office laptop (8 GB RAM or <= 4 threads without CUDA)
        if prof.ram_total_gb < 11.5 or (not prof.cuda_available and prof.cpu_threads <= 4):
            threads = max(2, min(4, prof.cpu_threads - 1 if prof.cpu_threads > 2 else prof.cpu_threads))
            if not prof.cuda_available:
                fallback_reasons.append("No NVIDIA CUDA GPU; using CPU int8 profile.")
            if prof.ram_total_gb < 11.5:
                fallback_reasons.append(f"Total RAM {prof.ram_total_gb:.1f} GB < 11.5 GB (Tier 1 conservative profile).")
            return ExecutionProfile(
                tier_id="tier1_minimum",
                tier_label="Tier 1: Minimum Office Laptop (8 GB / CPU-Only)",
                live_backend="faster_whisper",
                live_model="base",
                live_device="cpu",
                live_compute_type="int8",
                offline_backend="faster_whisper",
                offline_model="small",
                offline_device="cpu",
                offline_compute_type="int8",
                safe_cpu_threads=threads,
                live_best_effort=True,
                max_concurrent_models=1,
                hf_runtime_available=hf_avail,
                free_vram_mb=free_vram,
                fallback_reason=" ".join(fallback_reasons),
            )

        # Tier 3 candidate check: >= 15 GB RAM, >= 6 CPU threads, active NVIDIA CUDA & >= 2600 MB free VRAM
        has_tier3_gpu = (
            prof.ram_total_gb >= 15.0
            and prof.cpu_threads >= 6
            and prof.cuda_available
            and free_vram is not None
            and free_vram >= 2600
        )

        if has_tier3_gpu:
            threads = max(4, min(8, prof.cpu_threads - 2))
            want_hf_offline = hf_rc_enabled or (off_pref == "hf_qwen3_asr")
            if want_hf_offline and hf_avail and hf_ckpt_ok and off_pref != "faster_whisper":
                off_backend = "hf_qwen3_asr"
                off_model = hf_model_id
                off_dev = "cuda"
                off_ctype = "float16"
            else:
                if want_hf_offline and not hf_avail:
                    fallback_reasons.append("Optional PyTorch/Transformers HF ASR runtime not installed; using faster-whisper.")
                elif want_hf_offline and not hf_ckpt_ok:
                    fallback_reasons.append(f"HF checkpoint '{hf_model_id}' not cached locally; using faster-whisper.")
                off_backend = "faster_whisper"
                off_model = (
                    str(getattr(config, "whisper_model", "large-v3-turbo") or "large-v3-turbo")
                    if (config is not None and not adaptive_enabled)
                    else "large-v3-turbo"
                )
                off_dev = "cuda"
                off_ctype = "int8"

            if live_pref == "hf_qwen3_asr" and hf_avail and hf_ckpt_ok and free_vram >= 3200:
                live_backend = "hf_qwen3_asr"
                live_model = hf_model_id
                live_dev = "cuda"
                live_ctype = "float16"
            else:
                if live_pref == "hf_qwen3_asr" and free_vram < 3200:
                    fallback_reasons.append(f"Free VRAM {free_vram} MB < 3200 MB for HF live streaming; using Whisper Turbo.")
                live_backend = "faster_whisper"
                live_model = "large-v3-turbo"
                live_dev = "cuda"
                live_ctype = "int8"

            return ExecutionProfile(
                tier_id="tier3_performance",
                tier_label="Tier 3: Performance Laptop / Workstation (16-32 GB / CUDA GPU)",
                live_backend=live_backend,
                live_model=live_model,
                live_device=live_dev,
                live_compute_type=live_ctype,
                offline_backend=off_backend,
                offline_model=off_model,
                offline_device=off_dev,
                offline_compute_type=off_ctype,
                safe_cpu_threads=threads,
                live_best_effort=False,
                max_concurrent_models=1,
                hf_runtime_available=hf_avail,
                free_vram_mb=free_vram,
                fallback_reason=" ".join(fallback_reasons),
            )

        # Tier 2: Standard Office Laptop (16 GB class, integrated graphics or < 2600 MB free VRAM)
        threads = max(4, min(6, prof.cpu_threads - 2 if prof.cpu_threads > 4 else prof.cpu_threads))
        if not prof.cuda_available:
            fallback_reasons.append("Integrated graphics / CPU-only system; selecting CPU int8 profile.")
        elif free_vram is not None and free_vram < 2600:
            fallback_reasons.append(f"Free GPU VRAM ({free_vram} MB) < 2600 MB headroom; falling back cleanly to CPU int8.")

        off_model = "large-v3-turbo" if prof.ram_avail_gb >= 4.5 else "small"
        if prof.ram_avail_gb < 4.5:
            fallback_reasons.append(f"Available RAM ({prof.ram_avail_gb:.1f} GB) < 4.5 GB; offline model set to 'small'.")

        return ExecutionProfile(
            tier_id="tier2_standard",
            tier_label="Tier 2: Standard Office Laptop (16 GB / Integrated Graphics or Low VRAM)",
            live_backend="faster_whisper",
            live_model="small",
            live_device="cpu",
            live_compute_type="int8",
            offline_backend="faster_whisper",
            offline_model=off_model,
            offline_device="cpu",
            offline_compute_type="int8",
            safe_cpu_threads=threads,
            live_best_effort=True,
            max_concurrent_models=1,
            hf_runtime_available=hf_avail,
            free_vram_mb=free_vram,
            fallback_reason=" ".join(fallback_reasons),
        )

    @classmethod
    def _select_phase2_execution_profile(
        cls,
        *,
        prof: HardwareProfile,
        config: Any,
        models_dir: Optional[Path],
        free_vram: Optional[int],
        hf_avail: bool,
        hf_ckpt_ok: bool,
        hf_model_id: str,
    ) -> Optional[ExecutionProfile]:
        """Select Phase 2 hardware-aware live, offline, and selective-verifier backends."""
        try:
            from voxlocal.engine.onnx_asr_engines import get_phase2_runtime_capabilities

            caps = get_phase2_runtime_capabilities(models_dir)
        except Exception as exc:
            logger.debug("Phase 2 capability discovery unavailable: %s", exc)
            return None

        avail_models: Dict[str, bool] = dict(caps.get("models_available") or {})
        has_ort = bool(caps.get("onnxruntime"))
        has_tok = bool(caps.get("tokenizers"))
        has_sherpa = bool(caps.get("sherpa_onnx"))
        has_fw = bool(caps.get("faster_whisper"))

        raw_sess = str(getattr(config, "asr_session_mode", "auto") or "auto").strip().lower()
        cfg_lang = str(getattr(config, "language", "en+hi") or "en+hi").strip().lower()
        if raw_sess == "auto" and cfg_lang == "en":
            session_mode = "english"
        elif raw_sess in ("english", "hinglish", "auto"):
            session_mode = raw_sess
        else:
            session_mode = "auto"

        min_hf_vram = int(getattr(config, "gpu_min_free_vram_hf_mb", 2000) or 2000)
        min_ct2_vram = int(getattr(config, "gpu_min_free_vram_ct2_mb", 1100) or 1100)
        cuda_ok = bool(prof.cuda_available)
        req_dev = str(getattr(config, "compute_device", "auto") or "auto").strip().lower()
        if req_dev == "cpu":
            cuda_ok = False

        safe_threads = max(2, min(4, prof.cpu_threads - 1 if prof.cpu_threads > 2 else prof.cpu_threads))
        if prof.ram_total_gb < 11.5 or (not cuda_ok and prof.cpu_threads <= 4):
            tier_id = "tier1_minimum"
            tier_label = "Tier 1: CPU-Only / Entry Laptop (0 VRAM ONNX Profile)"
        elif cuda_ok and free_vram is not None and free_vram >= min_hf_vram and prof.ram_total_gb >= 15.0:
            tier_id = "tier3_performance"
            tier_label = "Tier 3: CUDA Accelerated Workstation (>= 2.0 GB Free VRAM)"
        else:
            tier_id = "tier2_standard"
            tier_label = "Tier 2: Standard Office Laptop (CPU INT8 / Bounded VRAM)"

        reasons: List[str] = []
        if not cuda_ok:
            reasons.append("CPU-only or CUDA disabled; running 0-VRAM CPU INT8 profile.")
        elif free_vram is not None and free_vram < min_hf_vram:
            reasons.append(f"Live free VRAM ({free_vram} MB) < {min_hf_vram} MB headroom for HF FP16.")

        sel_enabled = bool(getattr(config, "selective_second_pass_enabled", True))
        sel_pref = str(getattr(config, "selective_verifier_backend", "auto") or "auto").strip().lower()
        live_en_pref = str(getattr(config, "live_english_backend", "auto") or "auto").strip().lower()
        live_hi_pref = str(getattr(config, "live_hinglish_backend", "auto") or "auto").strip().lower()
        off_en_pref = str(getattr(config, "offline_english_backend", "auto") or "auto").strip().lower()
        off_hi_pref = str(getattr(config, "offline_hinglish_backend", "auto") or "auto").strip().lower()

        cfg_whisper = str(getattr(config, "whisper_model", "small") or "small").strip()
        if not has_fw:
            cfg_whisper = "small"

        # Determine English Session vs Hinglish/Auto Session
        if session_mode == "english":
            # Live English: prefer genuine frame-incremental Nemotron 560ms (0 VRAM)
            if live_en_pref in ("auto", "nemotron") and has_sherpa and avail_models.get("nemotron"):
                live_backend = "nemotron_onnx"
                live_model = "nemotron-streaming-en-0.6b-560ms-int8"
                live_dev, live_ctype = "cpu", "int8"
                live_exec_mode = "incremental"
            elif live_en_pref in ("auto", "parakeet", "nemotron") and has_sherpa and avail_models.get("parakeet"):
                live_backend = "parakeet_onnx"
                live_model = "parakeet-tdt-0.6b-v2-onnx-int8"
                live_dev, live_ctype = "cpu", "int8"
                live_exec_mode = "endpointed"
                reasons.append("Nemotron unavailable; using endpointed Parakeet ONNX CPU.")
            else:
                live_backend = "faster_whisper"
                if cuda_ok and (free_vram is None or free_vram >= min_ct2_vram):
                    live_model, live_dev, live_ctype = "large-v3-turbo", "cuda", "int8"
                else:
                    live_model = "small" if cfg_whisper in ("large-v3-turbo", "large-v3") else cfg_whisper
                    live_dev, live_ctype = "cpu", "int8"
                live_exec_mode = "endpointed"
                reasons.append("English ONNX models unavailable; using faster_whisper fallback.")

            # Offline English: prefer Parakeet TDT v2 (CPU 0.07x RTF)
            if off_en_pref in ("auto", "parakeet") and has_sherpa and avail_models.get("parakeet"):
                off_backend = "parakeet_onnx"
                off_model = "parakeet-tdt-0.6b-v2-onnx-int8"
                off_dev, off_ctype = "cpu", "int8"
            else:
                off_backend = "faster_whisper"
                if cuda_ok and (free_vram is None or free_vram >= min_ct2_vram):
                    off_model, off_dev, off_ctype = "large-v3-turbo", "cuda", "int8"
                else:
                    off_model = "large-v3-turbo" if prof.ram_avail_gb >= 4.5 else "small"
                    off_dev, off_ctype = "cpu", "int8"

            # Selective verifier for English (NEVER route English to Oriserve Apex)
            if not sel_enabled or sel_pref == "disabled":
                sel_verifier = "disabled"
            elif sel_pref in ("auto", "faster_whisper") and cuda_ok and (free_vram is None or free_vram >= min_ct2_vram) and has_fw:
                sel_verifier = "faster_whisper"
            elif sel_pref == "parakeet" and has_sherpa and avail_models.get("parakeet"):
                sel_verifier = "parakeet_onnx"
            else:
                sel_verifier = "disabled"

            return ExecutionProfile(
                tier_id=tier_id,
                tier_label=tier_label,
                live_backend=live_backend,
                live_model=live_model,
                live_device=live_dev,
                live_compute_type=live_ctype,
                offline_backend=off_backend,
                offline_model=off_model,
                offline_device=off_dev,
                offline_compute_type=off_ctype,
                safe_cpu_threads=safe_threads,
                live_best_effort=(live_dev == "cpu"),
                max_concurrent_models=1,
                hf_runtime_available=hf_avail,
                free_vram_mb=free_vram,
                fallback_reason=" ".join(reasons),
                session_mode=session_mode,
                live_execution_mode=live_exec_mode,
                selective_verifier_backend=sel_verifier,
                phase2_enabled=True,
                available_phase2_models=avail_models,
                post_call_verifier_backend=sel_verifier,
            )

        # Hinglish / Auto Session
        qwen3_onnx_ready = has_ort and has_tok and bool(avail_models.get("qwen3_onnx"))
        if live_hi_pref in ("auto", "qwen3_onnx") and qwen3_onnx_ready:
            live_backend = "qwen3_onnx"
            live_model = "qwen3-asr-0.6b-onnx-int8"
            live_dev, live_ctype = "cpu", "int8"
            live_exec_mode = "endpointed"
        else:
            live_backend = "faster_whisper"
            if cuda_ok and (free_vram is None or free_vram >= min_ct2_vram):
                live_model, live_dev, live_ctype = "large-v3-turbo", "cuda", "int8"
            else:
                live_model = "small" if cfg_whisper in ("large-v3-turbo", "large-v3") else cfg_whisper
                live_dev, live_ctype = "cpu", "int8"
            live_exec_mode = "endpointed"
            if live_hi_pref in ("auto", "qwen3_onnx") and not qwen3_onnx_ready:
                reasons.append("Guarded Qwen3 ONNX runtime/weights absent; falling back to faster_whisper live.")

        # Offline Hinglish / Auto
        can_hf_gpu = (
            cuda_ok
            and (free_vram is not None and free_vram >= min_hf_vram)
            and hf_avail
            and hf_ckpt_ok
            and prof.ram_total_gb >= 15.0
        )
        if off_hi_pref in ("auto", "hf_qwen3_asr") and can_hf_gpu and (
            off_hi_pref == "hf_qwen3_asr" or bool(getattr(config, "enable_hf_asr_rc", False)) or (free_vram is not None and free_vram >= min_hf_vram)
        ):
            off_backend = "hf_qwen3_asr"
            off_model = hf_model_id
            off_dev, off_ctype = "cuda", "float16"
        elif off_hi_pref in ("auto", "qwen3_onnx", "hf_qwen3_asr") and qwen3_onnx_ready:
            off_backend = "qwen3_onnx"
            off_model = "qwen3-asr-0.6b-onnx-int8"
            off_dev, off_ctype = "cpu", "int8"
        else:
            off_backend = "faster_whisper"
            if cuda_ok and (free_vram is None or free_vram >= min_ct2_vram):
                off_model, off_dev, off_ctype = "large-v3-turbo", "cuda", "int8"
            else:
                off_model = "large-v3-turbo" if prof.ram_avail_gb >= 4.5 else "small"
                off_dev, off_ctype = "cpu", "int8"
            if not qwen3_onnx_ready:
                reasons.append("Guarded Qwen3 ONNX unavailable; using faster_whisper offline.")

        # Selective verifier (opportunistic CUDA Oriserve Apex for Hinglish slices only)
        if not sel_enabled or sel_pref == "disabled":
            sel_verifier = "disabled"
        elif sel_pref in ("auto", "oriserve_apex") and cuda_ok and (free_vram is None or free_vram >= min_ct2_vram) and avail_models.get("oriserve_apex") and has_fw:
            sel_verifier = "oriserve_apex_ct2"
        elif sel_pref == "hf_qwen3_asr" and can_hf_gpu:
            sel_verifier = "hf_qwen3_asr"
        elif sel_pref == "faster_whisper" and cuda_ok and (free_vram is None or free_vram >= min_ct2_vram) and has_fw:
            sel_verifier = "faster_whisper"
        else:
            sel_verifier = "disabled"
            if cuda_ok and free_vram is not None and free_vram < min_ct2_vram and avail_models.get("oriserve_apex"):
                reasons.append(f"Free VRAM ({free_vram} MB) < {min_ct2_vram} MB; skipping CUDA 2nd-pass verifier.")

        allow_cpu_verifier = bool(getattr(config, "post_call_allow_cpu_verifier", True))
        if sel_verifier != "disabled":
            post_call_verifier = sel_verifier
        elif (
            sel_enabled
            and sel_pref in ("auto", "oriserve_apex")
            and allow_cpu_verifier
            and avail_models.get("oriserve_apex")
            and has_fw
        ):
            post_call_verifier = "oriserve_apex_ct2"
            reasons.append("Post-call Hinglish verifier (Oriserve Apex) enabled on CPU INT8.")
        else:
            post_call_verifier = "disabled"

        return ExecutionProfile(
            tier_id=tier_id,
            tier_label=tier_label,
            live_backend=live_backend,
            live_model=live_model,
            live_device=live_dev,
            live_compute_type=live_ctype,
            offline_backend=off_backend,
            offline_model=off_model,
            offline_device=off_dev,
            offline_compute_type=off_ctype,
            safe_cpu_threads=safe_threads,
            live_best_effort=(live_dev == "cpu"),
            max_concurrent_models=1,
            hf_runtime_available=hf_avail,
            free_vram_mb=free_vram,
            fallback_reason=" ".join(reasons),
            session_mode=session_mode,
            live_execution_mode=live_exec_mode,
            selective_verifier_backend=sel_verifier,
            phase2_enabled=True,
            available_phase2_models=avail_models,
            post_call_verifier_backend=post_call_verifier,
        )

    @classmethod
    def run_hardware_diagnostics(
        cls,
        run_short_benchmark: bool = False,
        config: Any = None,
    ) -> Dict[str, Any]:
        """Collect portable hardware, memory, GPU, and ExecutionProfile diagnostics for any Windows laptop."""
        prof = cls.get_profile(force_refresh=True)
        exec_prof = cls.select_execution_profile(config=config, force_refresh=False)
        used_mb, free_mb = cls._query_live_gpu_vram_mb()

        diag: Dict[str, Any] = {
            "platform": sys.platform,
            "os": f"{prof.os_name} (Build {prof.os_build})",
            "machine": platform.machine(),
            "cpu_name": prof.cpu_name,
            "cpu_cores_est": prof.cpu_cores,
            "cpu_threads_logical": prof.cpu_threads,
            "ram_total_gb": prof.ram_total_gb,
            "ram_available_gb": prof.ram_avail_gb,
            "disk_free_gb": prof.disk_free_gb,
            "gpu_name": prof.gpu_name,
            "gpu_type": prof.gpu_type,
            "cuda_available": prof.cuda_available,
            "gpu_vram_used_mb": used_mb,
            "gpu_vram_free_mb": free_mb,
            "hf_asr_runtime_available": cls._check_hf_asr_runtime_available(),
            "is_minimum_met": prof.is_minimum_met,
            "is_recommended_met": prof.is_recommended_met,
            "hardware_tier": exec_prof.tier_id,
            "execution_profile": exec_prof.to_dict(),
        }

        if run_short_benchmark:
            bench: Dict[str, Any] = {"executed": False}
            try:
                import numpy as np
                from voxlocal.engine.transcriber import LocalWhisperTranscriber

                model_name = exec_prof.live_model if exec_prof.live_backend == "faster_whisper" else "small"
                t0 = time.perf_counter()
                tr = LocalWhisperTranscriber(
                    models_dir=getattr(config, "models_dir", None) if config else None,
                    model_name=model_name,
                    compute_device="cpu",
                    compute_type="int8",
                    cpu_threads=exec_prof.safe_cpu_threads,
                )
                if tr.is_model_cached(model_name):
                    ok, msg = tr.ensure_loaded(model_name)
                    load_s = time.perf_counter() - t0
                    if ok and tr._model is not None:
                        clip_sec = 3.0
                        sr = 16000
                        t_arr = np.linspace(0, clip_sec, int(clip_sec * sr), dtype=np.float32)
                        synth = (0.02 * np.sin(2.0 * np.pi * 220.0 * t_arr)).astype(np.float32)
                        t_inf0 = time.perf_counter()
                        segs, _ = tr._model.transcribe(synth, beam_size=1, vad_filter=False)
                        list(segs)
                        inf_s = time.perf_counter() - t_inf0
                        tr.unload_model()
                        bench = {
                            "executed": True,
                            "model": model_name,
                            "device": "cpu",
                            "compute_type": "int8",
                            "threads": exec_prof.safe_cpu_threads,
                            "load_sec": round(load_s, 3),
                            "clip_sec": clip_sec,
                            "infer_sec": round(inf_s, 3),
                            "synthetic_rtf": round(inf_s / clip_sec, 4),
                        }
                    else:
                        bench = {"executed": False, "reason": msg}
                else:
                    bench = {"executed": False, "reason": f"Model '{model_name}' not cached locally"}
            except Exception as exc:
                bench = {"executed": False, "error": str(exc)}
            diag["short_cpu_benchmark"] = bench

        return diag

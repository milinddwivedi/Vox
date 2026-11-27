"""
VOX First-Run & Post-Install Local Health Check.

Validates genuine runtime, hardware compatibility, llama.cpp runtime availability,
and canonical AI model readiness without loading heavy models during startup.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from voxlocal import __version__
from voxlocal.core.hardware import HardwareProfiler
from voxlocal.core.logger import get_app_root, get_user_data_root, get_user_models_root
from voxlocal.engine.meeting_model_manager import resolve_llama_server_bin
from voxlocal.engine.model_manifest import (
    calculate_model_selection_summary,
    check_manifest_compatibility,
    get_model_entries,
    get_required_model_ids,
)
from voxlocal.engine.model_provisioner import ModelProvisioner


def run_first_run_health_check(
    config: Any = None,
    *,
    verify_hashes: bool = False,
) -> Dict[str, Any]:
    """Inspect runtime modules, manifest, hardware profile, and model completeness truthfully."""
    app_root = get_app_root()
    data_root = get_user_data_root()
    models_dir = (
        Path(config.models_dir)
        if config is not None and getattr(config, "models_dir", "")
        else get_user_models_root()
    )

    install_manifest_ok = True
    install_manifest_data: Dict[str, Any] = {}
    im_path = app_root / ".install_manifest.json"
    if im_path.is_file():
        try:
            install_manifest_data = json.loads(im_path.read_text(encoding="utf-8"))
            inst_ver = str(install_manifest_data.get("version") or "").strip()
            install_manifest_ok = (not inst_ver) or (inst_ver == str(__version__).strip())
        except Exception:
            install_manifest_ok = False

    required_modules = (
        "voxlocal.core.controller",
        "voxlocal.engine.transcriber",
        "voxlocal.engine.meeting_asr_pipeline",
        "voxlocal.engine.meeting_model_manager",
        "voxlocal.engine.llama_server_engine",
        "voxlocal.engine.knowledge_store",
        "onnxruntime",
        "tokenizers",
        "sherpa_onnx",
        "faster_whisper",
        "ctranslate2",
    )
    modules_ok: Dict[str, bool] = {}
    for mod in required_modules:
        try:
            importlib.import_module(mod)
            modules_ok[mod] = True
        except Exception:
            modules_ok[mod] = False
    runtime_ok = all(modules_ok.values())

    hw = HardwareProfiler.get_profile()
    srv_gpu, _ = resolve_llama_server_bin(prefer_gpu=True)
    srv_cpu, _ = resolve_llama_server_bin(prefer_gpu=False)
    llama_runtime_ok = (srv_cpu is not None) or (srv_gpu is not None)

    manifest_compatible = check_manifest_compatibility(str(__version__))
    provisioner = ModelProvisioner(models_dir=models_dir)
    entries = get_model_entries()
    selection_summary = calculate_model_selection_summary(ram_total_gb=hw.ram_total_gb)
    req_ids = set(selection_summary["selected_model_ids"])
    provisioning_plan = provisioner.estimate_provisioning_plan(ram_total_gb=hw.ram_total_gb)

    model_statuses: Dict[str, Any] = {}
    for mid in entries:
        # Check primary location cleanly without triggering heavy downloads
        st = provisioner.inspect_model(mid, verify_hash=verify_hashes)
        st["required_for_this_pc"] = mid in req_ids
        model_statuses[mid] = st

    # Dictation readiness can be satisfied by either large-v3-turbo or small fallback
    turbo_ok = bool(model_statuses.get("dictation_whisper_turbo", {}).get("installed"))
    small_ok = bool(model_statuses.get("dictation_whisper_small", {}).get("installed"))
    if not turbo_ok and not small_ok:
        try:
            from voxlocal.engine.transcriber import LocalWhisperTranscriber

            tr = LocalWhisperTranscriber(models_dir=models_dir, model_name="large-v3-turbo", compute_device="cpu")
            _, _, turbo_cached = tr.resolve_model_path("large-v3-turbo")
            _, _, small_cached = tr.resolve_model_path("small")
            turbo_ok = bool(turbo_cached)
            small_ok = bool(small_cached)
        except Exception:
            pass

    asr_ok = bool(model_statuses.get("meeting_asr", {}).get("installed"))
    dia_ok = bool(model_statuses.get("meeting_diarization", {}).get("installed"))
    llm_ok = bool(model_statuses.get("meeting_llm", {}).get("installed")) and llama_runtime_ok
    emb_ok = bool(model_statuses.get("meeting_embeddings", {}).get("installed"))

    features = {
        "vox_installed": runtime_ok and install_manifest_ok and manifest_compatible,
        "voice_dictation_ready": runtime_ok and (turbo_ok or small_ok),
        "meeting_transcription_ready": runtime_ok and asr_ok,
        "speaker_identification_ready": runtime_ok and dia_ok,
        "meeting_intelligence_ready": runtime_ok and llm_ok,
        "knowledge_ready": runtime_ok and emb_ok,
        "vulkan_acceleration_available": bool(srv_gpu is not None and hw.cuda_available),
        "cpu_fallback_ready": bool(srv_cpu is not None and asr_ok and dia_ok),
    }
    missing_components: List[str] = []
    if not runtime_ok:
        missing_components.extend([m for m, ok in modules_ok.items() if not ok])
    if not llama_runtime_ok:
        missing_components.append("runtime/llama_cpp")
    if not (turbo_ok or small_ok):
        missing_components.append(selection_summary["dictation_model_id"] or "dictation_whisper_turbo")
    if not asr_ok:
        missing_components.append("meeting_asr")
    if not dia_ok:
        missing_components.append("meeting_diarization")
    if not model_statuses.get("meeting_llm", {}).get("installed"):
        missing_components.append("meeting_llm")
    if not emb_ok:
        missing_components.append("meeting_embeddings")

    overall_ready = all(
        features[k]
        for k in (
            "vox_installed",
            "voice_dictation_ready",
            "meeting_transcription_ready",
            "speaker_identification_ready",
            "meeting_intelligence_ready",
            "knowledge_ready",
        )
    )

    return {
        "version": str(__version__),
        "frozen": bool(getattr(sys, "frozen", False)),
        "app_root": str(app_root),
        "data_root": str(data_root),
        "models_dir": str(models_dir),
        "install_manifest_ok": install_manifest_ok,
        "manifest_compatible": manifest_compatible,
        "runtime_ok": runtime_ok,
        "llama_runtime_vulkan": bool(srv_gpu is not None),
        "llama_runtime_cpu": bool(srv_cpu is not None),
        "selected_dictation_model": selection_summary["dictation_model_id"],
        "provisioning_estimate": {
            "selected_model_ids": provisioning_plan["selected_model_ids"],
            "total_selected_bytes": provisioning_plan["total_selected_bytes"],
            "total_selected_gb": provisioning_plan["total_selected_gb_decimal"],
            "already_present_bytes": provisioning_plan["already_present_bytes"],
            "download_needed_bytes": provisioning_plan["download_needed_bytes"],
            "download_needed_gb": provisioning_plan["download_needed_gb_decimal"],
            "required_free_disk_gb": provisioning_plan["required_free_disk_gb"],
        },
        "modules": modules_ok,
        "hardware": {
            "os": f"{hw.os_name} ({hw.os_build})",
            "cpu": hw.cpu_name,
            "ram_total_gb": hw.ram_total_gb,
            "ram_avail_gb": hw.ram_avail_gb,
            "gpu": hw.gpu_name,
            "disk_free_gb": hw.disk_free_gb,
            "tier": hw.corporate_tier,
            "min_requirements_met": hw.is_minimum_met,
        },
        "features": features,
        "models": model_statuses,
        "missing_components": missing_components,
        "overall_ready": overall_ready,
    }

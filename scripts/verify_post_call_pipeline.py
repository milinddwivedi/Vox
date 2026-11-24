"""
Post-Call High-Accuracy Pipeline & Live Preview Backpressure Benchmark (v1.0.13).

Evaluates the actual production classes in `src/voxlocal/`:
1. Post-Call CPU-Only Pipeline (ordinary customer laptop simulation, 0 GPU dependency):
   - GuardedQwen3OnnxEngine across EN, IN-EN, HI, Hinglish, M#234 (16/16), M#235 (111/111)
   - Adaptive ParakeetOnnxEngine across EN & IN-EN tracks
   - SelectiveSecondPassVerifier + OriserveApexVerifierEngine(allow_cpu=True) forced CPU INT8
     across coshe_hinglish, fleurs_hi_in, monsoon_in_en, m234_full_90s, m235_verified_111
2. Post-Call CUDA Pipeline (when live free VRAM >= 1100 MB):
   - SelectiveSecondPassVerifier + OriserveApexVerifierEngine on CUDA INT8
3. Live Preview Backpressure & Latency Test:
   - NemotronStreamingEngine genuine incremental streaming on AMI English
   - GuardedQwen3OnnxEngine bounded-backlog endpointed preview on M#234 + synthetic stall injection

Reuses exact Phase 1.5 corpus manifest & scoring functions without touching ground truth or data/voxlocal.db.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"
for _p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from benchmark_phase15_validation import (
    CORPUS15_MANIFEST,
    _finalize_track_bundle_15,
    _score_clip_15,
)
from benchmark_phase1_asr import (
    _score_official_meeting,
    align_clip_details,
    compute_target_db_rows_sha256,
)
from benchmark_speech_intelligence import get_process_rss_mb, query_gpu_vram
from score_ground_truth import score_predictions_against_reference

from voxlocal.audio.devices import TARGET_SAMPLE_RATE
from voxlocal.core.config import AppConfig
from voxlocal.core.hardware import ExecutionProfile, HardwareProfiler
import voxlocal.engine.onnx_asr_engines as onnx_mod
from voxlocal.engine.onnx_asr_engines import GuardedQwen3OnnxEngine, ParakeetOnnxEngine
from voxlocal.engine.selective_verifier import SelectiveSecondPassVerifier
from voxlocal.engine.streaming_transcriber import LiveMeetingStreamingTranscriber
from voxlocal.engine.transcriber import LocalWhisperTranscriber, load_wav_as_16k_f32
from voxlocal.storage.database import LocalDatabase

OUT_JSON = ROOT_DIR / "data" / "benchmarks" / "post_call_v1013_verification.json"
OUT_MD = ROOT_DIR / "data" / "benchmarks" / "post_call_v1013_verification.md"


class _ReplayRecorder:
    """Deterministic time-paced dual/single-track WAV replay compatible with LiveMeetingStreamingTranscriber."""

    def __init__(self, mic_wav: Optional[str] = None, sys_wav: Optional[str] = None) -> None:
        self.mic_arr = load_wav_as_16k_f32(mic_wav) if mic_wav and os.path.exists(mic_wav) else np.zeros(0, dtype=np.float32)
        self.sys_arr = load_wav_as_16k_f32(sys_wav) if sys_wav and os.path.exists(sys_wav) else np.zeros(0, dtype=np.float32)
        self.total_dur = max(self.mic_arr.size, self.sys_arr.size) / float(TARGET_SAMPLE_RATE)
        self.elapsed_seconds = 0.0

    def get_audio_window(self, start_sec: float, end_sec: float) -> Tuple[np.ndarray, np.ndarray, float]:
        a = max(0, int(round(start_sec * TARGET_SAMPLE_RATE)))
        b = max(a, int(round(end_sec * TARGET_SAMPLE_RATE)))
        m_sl = self.mic_arr[min(a, self.mic_arr.size) : min(b, self.mic_arr.size)].copy() if self.mic_arr.size else np.zeros(0, dtype=np.float32)
        s_sl = self.sys_arr[min(a, self.sys_arr.size) : min(b, self.sys_arr.size)].copy() if self.sys_arr.size else np.zeros(0, dtype=np.float32)
        return m_sl, s_sl, round(a / float(TARGET_SAMPLE_RATE), 3)


def _eval_engine_tracks(
    engine: Any,
    track_names: List[str],
    manifest: Dict[str, Any],
    *,
    is_parakeet: bool = False,
) -> Dict[str, Any]:
    rss0 = get_process_rss_mb()
    vram0 = query_gpu_vram()
    t_wall0 = time.perf_counter()
    t_cpu0 = time.process_time()

    ok, msg = engine.ensure_loaded()
    if not ok:
        raise RuntimeError(msg)

    peak_rss = max(rss0, get_process_rss_mb())
    tracks_res: Dict[str, Any] = {}
    raw_items: Dict[str, List[Dict[str, Any]]] = {}

    for t_name in track_names:
        clips = list(manifest["tracks"][t_name])
        print(f"[PostCallBench] Running {engine.model_label} on '{t_name}' ({len(clips)} clips)...", flush=True)
        recs: List[Dict[str, Any]] = []
        raw_out_list: List[Dict[str, Any]] = []
        t_track0 = time.perf_counter()
        audio_sec_sum = 0.0
        for idx, clip in enumerate(clips, 1):
            arr = load_wav_as_16k_f32(clip["wav_path"])
            audio_sec_sum += float(arr.size) / float(TARGET_SAMPLE_RATE)
            out = engine.transcribe_array(arr)
            raw_txt = str(out.get("raw_text") or out.get("text") or "")
            disp_txt = str(out.get("roman_text") or out.get("text") or raw_txt)
            scored_in = {
                "raw_text": raw_txt,
                "roman_text": disp_txt,
                "detected_language": out.get("detected_language") or ("en" if is_parakeet else ""),
                "language_probability": out.get("language_probability"),
                "lang_probs": out.get("lang_probs"),
                "avg_logprob": out.get("avg_logprob"),
                "min_token_prob": out.get("min_token_prob"),
                "infer_sec": float(out.get("infer_sec") or 0.0),
            }
            rec = _score_clip_15(t_name, clip, scored_in)
            recs.append(rec)
            raw_out_list.append({"clip": clip, "seg": scored_in, "rec": rec})
            if idx % 20 == 0 or idx == len(clips):
                peak_rss = max(peak_rss, get_process_rss_mb())
        tracks_res[t_name] = _finalize_track_bundle_15(t_name, recs)
        raw_items[t_name] = raw_out_list
        m = tracks_res[t_name]["metrics"]
        m["wall_sec"] = round(time.perf_counter() - t_track0, 2)
        m["audio_sec"] = round(audio_sec_sum, 2)
        m["extrapolated_30m_min"] = round(float(m.get("rtf") or 0.0) * 30.0, 2)
        print(
            f"  -> {t_name}: WER={m['wer']:.4f} RTF={m['rtf']:.3f} est_30m={m['extrapolated_30m_min']:.1f}m omit={m['omitted_clip_pct']}%",
            flush=True,
        )

    wall_s = time.perf_counter() - t_wall0
    cpu_s = time.process_time() - t_cpu0
    vram_pk = query_gpu_vram()
    engine.unload_model()
    gc.collect()
    return {
        "model": engine.model_label,
        "backend": engine.asr_backend,
        "device": engine.asr_device,
        "compute_type": engine.asr_compute_type,
        "load_sec": engine.load_sec,
        "wall_sec": round(wall_s, 2),
        "cpu_sec": round(cpu_s, 2),
        "avg_cpu_pct": round((cpu_s / max(0.01, wall_s)) * 100.0, 1),
        "peak_rss_mb": round(peak_rss, 1),
        "vram_before_mb": vram0.get("used_mb"),
        "vram_peak_mb": vram_pk.get("used_mb"),
        "tracks": {k: v["metrics"] for k, v in tracks_res.items()},
        "_track_bundles": tracks_res,
        "_raw_items": raw_items,
    }


def _run_selective_pass(
    q3_res: Dict[str, Any],
    manifest: Dict[str, Any],
    *,
    force_cpu: bool,
    tracks_to_verify: Tuple[str, ...],
    selective_max_utts: int = 200,
) -> Dict[str, Any]:
    q3_raw_items = q3_res["_raw_items"]
    if not force_cpu:
        _, free_mb = HardwareProfiler._query_live_gpu_vram_mb()
        if free_mb is not None and free_mb < 1100:
            return {"skipped": True, "reason": f"Live free VRAM ({free_mb} MB) < 1100 MB; CUDA pass skipped."}
    else:
        free_mb = None

    models_dir = ROOT_DIR / "data" / "models"
    verifier = SelectiveSecondPassVerifier(models_dir=models_dir)
    ep = ExecutionProfile(
        tier_id="tier2_cpu" if force_cpu else "tier3_cuda",
        tier_label="CPU INT8 Post-Call Verifier" if force_cpu else "CUDA INT8 Post-Call Verifier",
        live_backend="qwen3_onnx",
        live_model="qwen3-asr-0.6b-onnx-int8",
        live_device="cpu",
        live_compute_type="int8",
        offline_backend="qwen3_onnx",
        offline_model="qwen3-asr-0.6b-onnx-int8",
        offline_device="cpu",
        offline_compute_type="int8",
        safe_cpu_threads=4,
        live_best_effort=True,
        max_concurrent_models=1,
        hf_runtime_available=False,
        free_vram_mb=free_mb,
        session_mode="auto",
        selective_verifier_backend="disabled" if force_cpu else "oriserve_apex_ct2",
        phase2_enabled=True,
        post_call_verifier_backend="oriserve_apex_ct2",
    )
    cfg = AppConfig(
        models_dir=str(models_dir),
        selective_max_utterances_per_meeting=int(selective_max_utts),
        post_call_allow_cpu_verifier=True,
    )
    results: Dict[str, Any] = {}
    official_meetings: Dict[str, Any] = {}
    vram_before = query_gpu_vram()
    verifier_dev_seen = ""

    gap_zeros = np.zeros(int(0.5 * TARGET_SAMPLE_RATE), dtype=np.float32)

    def _run_all() -> None:
        nonlocal verifier_dev_seen
        with tempfile.TemporaryDirectory(prefix="postcall_ver_") as tmp_dir:
            tmp_root = Path(tmp_dir)
            for t_name in tracks_to_verify:
                items = q3_raw_items.get(t_name, [])
                t0 = time.perf_counter()
                audio_parts: List[np.ndarray] = []
                track_segs: List[Dict[str, Any]] = []
                cur_sample = 0
                audio_sec_sum = 0.0
                for item in items:
                    clip = item["clip"]
                    clip_arr = load_wav_as_16k_f32(clip["wav_path"])
                    audio_sec_sum += float(clip_arr.size) / float(TARGET_SAMPLE_RATE)
                    s_sec = cur_sample / float(TARGET_SAMPLE_RATE)
                    e_sec = (cur_sample + int(clip_arr.size)) / float(TARGET_SAMPLE_RATE)
                    audio_parts.append(clip_arr)
                    audio_parts.append(gap_zeros)
                    cur_sample += int(clip_arr.size) + int(gap_zeros.size)
                    track_segs.append(
                        {
                            "start_time": round(s_sec, 4),
                            "end_time": round(e_sec, 4),
                            "speaker": clip.get("speaker", "Speaker"),
                            "source_track": "mixed",
                            "raw_text": item["seg"]["raw_text"],
                            "normalized_text": item["seg"]["roman_text"],
                            "text": item["seg"]["roman_text"],
                            "raw_model_score": item["seg"].get("avg_logprob"),
                            "detected_language": item["seg"].get("detected_language", ""),
                            "asr_backend": "qwen3_onnx",
                            "processing_history": [],
                        }
                    )

                track_wav = tmp_root / f"{t_name}.wav"
                joined = np.concatenate(audio_parts) if audio_parts else np.zeros(TARGET_SAMPLE_RATE, dtype=np.float32)
                pcm = (np.clip(joined, -1.0, 1.0) * 32767.0).astype(np.int16)
                with wave.open(str(track_wav), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(TARGET_SAMPLE_RATE)
                    wf.writeframes(pcm.tobytes())

                out_segs, st = verifier.verify_segments(
                    track_segs,
                    execution_profile=ep,
                    config=cfg,
                    mixed_wav_path=str(track_wav),
                    verifier_backend_override="oriserve_apex_ct2",
                )
                ver_wall = time.perf_counter() - t0
                if st.get("verifier_device"):
                    verifier_dev_seen = str(st["verifier_device"])

                recs_out: List[Dict[str, Any]] = []
                for item, s_fin in zip(items, out_segs):
                    clip = item["clip"]
                    assert s_fin["raw_text"] == item["seg"]["raw_text"], f"raw_text mutated on {clip['id']}!"
                    fin_out = {
                        "raw_text": s_fin["raw_text"],
                        "roman_text": s_fin["text"],
                        "detected_language": s_fin.get("detected_language", ""),
                        "avg_logprob": s_fin.get("raw_model_score"),
                        "infer_sec": item["seg"]["infer_sec"],
                    }
                    recs_out.append(_score_clip_15(t_name, clip, fin_out))

                bundle = _finalize_track_bundle_15(t_name, recs_out)
                m = bundle["metrics"]
                q3_wall = float(q3_res["tracks"][t_name].get("wall_sec") or 0.0)
                combined_wall = q3_wall + ver_wall
                combined_rtf = combined_wall / max(0.01, audio_sec_sum)
                m["escalated_count"] = int(st.get("escalated_count", 0))
                m["escalated_pct"] = round((m["escalated_count"] / max(1, len(items))) * 100.0, 2)
                m["accepted_count"] = int(st.get("accepted_count", 0))
                m["rejected_count"] = int(st.get("rejected_count", 0))
                m["skipped_guard_count"] = int(st.get("skipped_guard_count", 0))
                m["verifier_device"] = str(st.get("verifier_device") or verifier_dev_seen or ("cpu" if force_cpu else "cuda"))
                m["verify_wall_sec"] = round(ver_wall, 2)
                m["combined_post_call_wall_sec"] = round(combined_wall, 2)
                m["combined_post_call_rtf"] = round(combined_rtf, 4)
                m["extrapolated_30m_min"] = round(combined_rtf * 30.0, 2)
                m["errors"] = list(st.get("errors") or [])
                results[t_name] = m

                if t_name == "m234_full_90s":
                    official_meetings["m234"] = _score_official_meeting(
                        Path(manifest["full_meetings"]["m234"]["ref_path"]),
                        recs_out,
                    )
                elif t_name == "m235_verified_111":
                    official_meetings["m235"] = _score_official_meeting(
                        Path(manifest["full_meetings"]["m235"]["ref_path"]),
                        recs_out,
                    )

                mode_tag = "CPU" if force_cpu else "CUDA"
                print(
                    f"[PostCall Verifier {mode_tag}] {t_name}: WER={m['wer']:.4f} esc={m['escalated_count']}/{len(items)} acc={m['accepted_count']} dev={m['verifier_device']} combined_RTF={m['combined_post_call_rtf']:.3f} est_30m={m['extrapolated_30m_min']:.1f}m",
                    flush=True,
                )

    if force_cpu:
        with patch.object(onnx_mod, "is_cuda_runtime_available", return_value=False), \
             patch.object(HardwareProfiler, "_query_live_gpu_vram_mb", return_value=(None, None)):
            _run_all()
    else:
        _run_all()

    vram_after = query_gpu_vram()
    return {
        "skipped": False,
        "mode": "cpu_int8" if force_cpu else "cuda_int8",
        "verifier_device": verifier_dev_seen or ("cpu" if force_cpu else "cuda"),
        "vram_before_mb": vram_before.get("used_mb"),
        "vram_after_unload_mb": vram_after.get("used_mb"),
        "tracks": results,
        "official_meetings": official_meetings,
    }


def _eval_live_backpressure(manifest: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="postcall_stream_") as td:
        tmp = Path(td)
        db = LocalDatabase(tmp / "stream_eval.db")
        cfg = AppConfig(
            storage_dir=str(tmp),
            models_dir=str(ROOT_DIR / "data" / "models"),
            enable_phase2_asr_pipeline=True,
            live_preview_enabled=True,
            live_preview_max_backlog_sec=8.0,
        )
        dummy_tr = LocalWhisperTranscriber(models_dir=ROOT_DIR / "data" / "models", model_name="small", compute_device="cpu")

        # 1. Incremental English (Nemotron) on AMI continuous
        ami_cont = manifest["continuous_streams"]["ami_meetings_continuous"]
        rec_ami = _ReplayRecorder(sys_wav=ami_cont["wav_path"])
        mid_ami = db.create_meeting(title="AMI Incremental Preview", model_used="nemotron")
        partials_ami: List[Dict[str, Any]] = []
        confirmed_ami: List[Dict[str, Any]] = []
        st_ami = LiveMeetingStreamingTranscriber(
            transcriber=dummy_tr,
            db=db,
            on_partial=lambda m, d: partials_ami.append(dict(d)),
            on_confirmed=lambda m, d: confirmed_ami.append(dict(d)),
            config=cfg,
        )
        ep_en = ExecutionProfile(
            tier_id="tier2", tier_label="T2", live_backend="nemotron_onnx",
            live_model="nemotron-streaming-en-0.6b-560ms-int8", live_device="cpu", live_compute_type="int8",
            offline_backend="parakeet_onnx", offline_model="parakeet-tdt-0.6b-v2-onnx-int8",
            offline_device="cpu", offline_compute_type="int8",
            safe_cpu_threads=4, live_best_effort=True, max_concurrent_models=1,
            hf_runtime_available=False, free_vram_mb=None,
            session_mode="english", live_execution_mode="incremental", phase2_enabled=True,
        )
        t0 = time.perf_counter()
        st_ami.start(mid_ami, rec_ami, language="en", execution_profile=ep_en, session_mode="english")
        st_ami._stop_event.set()
        if st_ami._worker_thread:
            st_ami._worker_thread.join(timeout=1.0)
        st_ami._stop_event.clear()
        st_ami._ensure_phase2_engine_loaded()

        cur_t = 0.0
        while cur_t < rec_ami.total_dur:
            cur_t = min(rec_ami.total_dur, cur_t + 0.56)
            rec_ami.elapsed_seconds = cur_t
            st_ami._step_phase2_incremental()
        st_ami._active = True
        st_ami.stop()
        wall_ami = time.perf_counter() - t0
        tel_ami = st_ami.get_stream_telemetry()
        pred_ami = " ".join(c["text"] for c in confirmed_ami if c.get("text"))
        al_ami = align_clip_details(ami_cont["reference_text"], pred_ami, mode="strict")
        tel_ami["wer"] = round(al_ami["edits"] / max(1, al_ami["ref_words"]), 4)
        tel_ami["audio_duration_sec"] = round(rec_ami.total_dur, 2)
        tel_ami["wall_sec"] = round(wall_ami, 2)
        tel_ami["rtf"] = round(wall_ami / max(0.1, rec_ami.total_dur), 4)
        tel_ami["partial_emissions_total"] = len(partials_ami)
        out["english_incremental_nemotron_ami"] = tel_ami

        # 2. Bounded-Backlog Guarded Qwen3 Preview on M#234 + synthetic 14s backlog stall
        m234_meta = manifest["full_meetings"]["m234"]
        m234_ref = json.loads(Path(m234_meta["ref_path"]).read_text(encoding="utf-8"))
        rec_234 = _ReplayRecorder(mic_wav=m234_meta["mic"], sys_wav=m234_meta["system"])
        mid_234 = db.create_meeting(title="M234 Backpressure Preview", model_used="qwen3-onnx")
        confirmed_234: List[Dict[str, Any]] = []
        partials_234: List[Dict[str, Any]] = []
        st_234 = LiveMeetingStreamingTranscriber(
            transcriber=dummy_tr,
            db=db,
            on_partial=lambda m, d: partials_234.append(dict(d)),
            on_confirmed=lambda m, d: confirmed_234.append(dict(d)),
            config=cfg,
        )
        ep_hi = ExecutionProfile(
            tier_id="tier2", tier_label="T2", live_backend="qwen3_onnx",
            live_model="qwen3-asr-0.6b-onnx-int8", live_device="cpu", live_compute_type="int8",
            offline_backend="qwen3_onnx", offline_model="qwen3-asr-0.6b-onnx-int8",
            offline_device="cpu", offline_compute_type="int8",
            safe_cpu_threads=4, live_best_effort=True, max_concurrent_models=1,
            hf_runtime_available=False, free_vram_mb=None,
            session_mode="auto", live_execution_mode="endpointed", phase2_enabled=True,
        )
        t1 = time.perf_counter()
        st_234.start(mid_234, rec_234, language="en+hi", remote_speaker="Hemanth Kumar Talathoti", execution_profile=ep_hi, session_mode="auto")
        st_234._stop_event.set()
        if st_234._worker_thread:
            st_234._worker_thread.join(timeout=1.0)
        st_234._stop_event.clear()
        st_234._ensure_phase2_engine_loaded()

        # Normal pace up to 20s
        cur_t = 0.0
        while cur_t < min(20.0, rec_234.total_dur):
            cur_t = min(rec_234.total_dur, cur_t + 0.45)
            rec_234.elapsed_seconds = cur_t
            st_234._step_phase2_endpointed()
        # Simulate slow-CPU stall where recorder advances +12s before next poll
        if rec_234.total_dur > 35.0:
            cur_t = min(rec_234.total_dur, cur_t + 12.0)
            rec_234.elapsed_seconds = cur_t
            st_234._step_phase2_endpointed()
        while cur_t < rec_234.total_dur:
            cur_t = min(rec_234.total_dur, cur_t + 0.45)
            rec_234.elapsed_seconds = cur_t
            st_234._step_phase2_endpointed()
        st_234._active = True
        st_234.stop()
        wall_234 = time.perf_counter() - t1
        tel_234 = st_234.get_stream_telemetry()
        m234_scores = score_predictions_against_reference(m234_ref, confirmed_234)
        off_prim = m234_scores.get("primary_masked_uncertain", m234_scores)
        tel_234["m234_preview_norm_wer"] = off_prim.get("stream_hinglish_norm_wer")
        tel_234["audio_duration_sec"] = round(rec_234.total_dur, 2)
        tel_234["wall_sec"] = round(wall_234, 2)
        tel_234["rtf"] = round(wall_234 / max(0.1, rec_234.total_dur), 4)
        tel_234["partial_emissions_total"] = len(partials_234)
        out["hinglish_bounded_preview_m234_stall_injected"] = tel_234
        print(
            f"[LivePreview Backpressure] dropped_windows={tel_234['dropped_backlog_windows']} max_backlog={tel_234['max_backlog_sec']}s commits={tel_234['committed_segments']}",
            flush=True,
        )
        db.close()
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Verify v1.0.13 post-call & live backpressure pipeline.")
    parser.add_argument(
        "--fast-v1013",
        action="store_true",
        help="Run representative EN/IN-EN/M234/M235 verification with production 40-utt CPU cap + CUDA + live backpressure (~15-20m).",
    )
    args = parser.parse_args(argv)
    db_before = compute_target_db_rows_sha256()
    manifest = json.loads(CORPUS15_MANIFEST.read_text(encoding="utf-8"))
    models_dir = ROOT_DIR / "data" / "models"
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    # 1. Guarded Qwen3 ONNX Primary Post-Call (CPU INT8)
    if args.fast_v1013:
        q3_tracks = [
            "ami_meetings_en",
            "monsoon_in_en",
            "m234_full_90s",
            "m235_verified_111",
        ]
        pk_tracks = ["ami_meetings_en", "monsoon_in_en"]
        cpu_tracks = ("monsoon_in_en", "m234_full_90s", "m235_verified_111")
        cpu_max_utts = 40
        cuda_tracks = ("m234_full_90s", "m235_verified_111")
    else:
        q3_tracks = [
            "ami_meetings_en",
            "fleurs_en_us",
            "earnings22_us",
            "monsoon_in_en",
            "nptel_in_en",
            "fleurs_hi_in",
            "coshe_hinglish",
            "m234_full_90s",
            "m235_verified_111",
        ]
        pk_tracks = ["ami_meetings_en", "fleurs_en_us", "earnings22_us", "monsoon_in_en", "nptel_in_en"]
        cpu_tracks = ("coshe_hinglish", "fleurs_hi_in", "monsoon_in_en", "m234_full_90s", "m235_verified_111")
        cpu_max_utts = 200
        cuda_tracks = ("coshe_hinglish", "m234_full_90s", "m235_verified_111")

    q3_eng = GuardedQwen3OnnxEngine(models_dir=models_dir, num_threads=4)
    q3_res = _eval_engine_tracks(q3_eng, q3_tracks, manifest, is_parakeet=False)
    q3_res["official_meetings"] = {
        "m234": _score_official_meeting(Path(manifest["full_meetings"]["m234"]["ref_path"]), q3_res["_track_bundles"]["m234_full_90s"]["clips"]),
        "m235": _score_official_meeting(Path(manifest["full_meetings"]["m235"]["ref_path"]), q3_res["_track_bundles"]["m235_verified_111"]["clips"]),
    }

    # 2. Adaptive English Parakeet ONNX Post-Call (CPU INT8, EN + IN-EN)
    pk_eng = ParakeetOnnxEngine(models_dir=models_dir, num_threads=4)
    pk_res = _eval_engine_tracks(pk_eng, pk_tracks, manifest, is_parakeet=True)

    # 3. Post-Call CPU-Only Selective Verifier (Oriserve Apex forced CPU INT8)
    cpu_ver_res = _run_selective_pass(
        q3_res,
        manifest,
        force_cpu=True,
        tracks_to_verify=cpu_tracks,
        selective_max_utts=cpu_max_utts,
    )

    # 4. Post-Call CUDA Selective Verifier (Oriserve Apex CUDA INT8 if free VRAM >= 1100 MB)
    cuda_ver_res = _run_selective_pass(
        q3_res,
        manifest,
        force_cpu=False,
        tracks_to_verify=cuda_tracks,
        selective_max_utts=200,
    )

    # 5. Live Preview Backpressure & Incremental Streaming Test
    stream_res = _eval_live_backpressure(manifest)

    db_after = compute_target_db_rows_sha256()
    assert db_before == db_after, f"data/voxlocal.db rows mutated during benchmark: {db_before} != {db_after}"

    summary = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "db_sha256_verified": db_after,
        "post_call_primary_qwen3_onnx_cpu": {k: v for k, v in q3_res.items() if not k.startswith("_")},
        "post_call_adaptive_english_parakeet_cpu": {k: v for k, v in pk_res.items() if not k.startswith("_")},
        "post_call_verifier_oriserve_cpu": cpu_ver_res,
        "post_call_verifier_oriserve_cuda": cuda_ver_res,
        "live_preview_backpressure": stream_res,
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    # Build comprehensive Markdown Verification Report
    md: List[str] = [
        "# VOX v1.0.13 — Post-Call High-Accuracy & Live Backpressure Benchmark Verification",
        "",
        f"- Generated UTC: `{summary['generated_at_utc']}`",
        f"- Protected `data/voxlocal.db` SHA-256 unchanged: `{db_after}`",
        "",
        "## 1. Post-Call Primary Engine: Guarded Qwen3 ONNX (`qwen3-asr-0.6b-onnx-int8`, CPU INT8)",
        f"- Peak RSS: `{q3_res['peak_rss_mb']} MB` | Load time: `{q3_res['load_sec']}s`",
        "",
        "| Track | WER | RTF | Est. 30-min Process Time | Omit % |",
        "|---|---:|---:|---:|---:|",
    ]
    for tn in q3_tracks:
        tm = q3_res["tracks"][tn]
        md.append(f"| `{tn}` | {tm['wer']:.4f} | {tm['rtf']:.3f}x | {tm['extrapolated_30m_min']:.1f} min | {tm['omitted_clip_pct']}% |")

    md.extend([
        "",
        "## 2. Post-Call Adaptive English Upgrade: Parakeet TDT ONNX (`parakeet-tdt-0.6b-v2-onnx-int8`, CPU INT8)",
        f"- Peak RSS: `{pk_res['peak_rss_mb']} MB` | Load time: `{pk_res['load_sec']}s`",
        "",
        "| Track | Qwen3 ONNX WER | Parakeet ONNX WER | Parakeet RTF | Est. 30-min Process Time |",
        "|---|---:|---:|---:|---:|",
    ])
    for tn in pk_tracks:
        qm = q3_res["tracks"][tn]
        pm = pk_res["tracks"][tn]
        md.append(f"| `{tn}` | {qm['wer']:.4f} | {pm['wer']:.4f} | {pm['rtf']:.3f}x | {pm['extrapolated_30m_min']:.1f} min |")

    md.extend([
        "",
        "## 3. Post-Call CPU-Only Selective Verifier (`OriserveApexVerifierEngine(allow_cpu=True)`, CPU INT8)",
        f"- Device Used: `{cpu_ver_res.get('verifier_device')}`",
        "",
        "| Track | Primary WER | Post-Call Verified WER | Escalated | Accepted | Combined RTF | Est. 30-min Total |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for tn, cm in (cpu_ver_res.get("tracks") or {}).items():
        qm = q3_res["tracks"][tn]
        md.append(
            f"| `{tn}` | {qm['wer']:.4f} | {cm['wer']:.4f} | {cm['escalated_count']}/{len(manifest['tracks'][tn])} ({cm['escalated_pct']}%) | {cm['accepted_count']} | {cm['combined_post_call_rtf']:.3f}x | {cm['extrapolated_30m_min']:.1f} min |"
        )

    m234_q3 = q3_res["official_meetings"]["m234"].get("primary_masked_uncertain", q3_res["official_meetings"]["m234"])
    m235_q3 = q3_res["official_meetings"]["m235"].get("primary_masked_uncertain", q3_res["official_meetings"]["m235"])
    m234_cpu = cpu_ver_res["official_meetings"]["m234"].get("primary_masked_uncertain", cpu_ver_res["official_meetings"]["m234"])
    m235_cpu = cpu_ver_res["official_meetings"]["m235"].get("primary_masked_uncertain", cpu_ver_res["official_meetings"]["m235"])
    md.extend([
        "",
        "### Verified Meeting Ground-Truth Comparison (M#234 16/16 & M#235 111/111)",
        "| Meeting | Qwen3 ONNX Norm WER | + Post-Call Oriserve CPU INT8 Norm WER |",
        "|---|---:|---:|",
        f"| `M#234 (16/16 verified)` | {m234_q3.get('stream_hinglish_norm_wer'):.4f} | {m234_cpu.get('stream_hinglish_norm_wer'):.4f} |",
        f"| `M#235 (111/111 verified)` | {m235_q3.get('stream_hinglish_norm_wer'):.4f} | {m235_cpu.get('stream_hinglish_norm_wer'):.4f} |",
    ])

    md.extend(["", "## 4. Post-Call CUDA Selective Verifier (`OriserveApexVerifierEngine`, CUDA INT8)"])
    if cuda_ver_res.get("skipped"):
        md.append(f"- Skipped: {cuda_ver_res.get('reason')}")
    else:
        md.extend([
            f"- Device Used: `{cuda_ver_res.get('verifier_device')}` | VRAM before/after unload: `{cuda_ver_res.get('vram_before_mb')} MB` / `{cuda_ver_res.get('vram_after_unload_mb')} MB`",
            "",
            "| Track | Primary WER | Post-Call CUDA Verified WER | Accepted | Combined RTF | Est. 30-min Total |",
            "|---|---:|---:|---:|---:|---:|",
        ])
        for tn, gm in (cuda_ver_res.get("tracks") or {}).items():
            qm = q3_res["tracks"][tn]
            md.append(
                f"| `{tn}` | {qm['wer']:.4f} | {gm['wer']:.4f} | {gm['accepted_count']} | {gm['combined_post_call_rtf']:.3f}x | {gm['extrapolated_30m_min']:.1f} min |"
            )
        m234_g = cuda_ver_res["official_meetings"]["m234"].get("primary_masked_uncertain", cuda_ver_res["official_meetings"]["m234"])
        m235_g = cuda_ver_res["official_meetings"]["m235"].get("primary_masked_uncertain", cuda_ver_res["official_meetings"]["m235"])
        md.append(f"- `M#234 CUDA Norm WER`: `{m234_g.get('stream_hinglish_norm_wer'):.4f}` | `M#235 CUDA Norm WER`: `{m235_g.get('stream_hinglish_norm_wer'):.4f}`")

    nem = stream_res["english_incremental_nemotron_ami"]
    qbp = stream_res["hinglish_bounded_preview_m234_stall_injected"]
    md.extend([
        "",
        "## 5. Live Preview Isolation & Backpressure Test",
        f"- **Nemotron Incremental (AMI EN):** mode=`{nem['execution_mode']}`, first_partial=`{nem['first_partial_latency_ms']} ms`, partials=`{nem['partial_emissions_total']}`, commits=`{nem['committed_segments']}`, WER=`{nem['wer']:.4f}`, RTF=`{nem['rtf']:.3f}x`",
        f"- **Bounded Preview (Qwen3 M#234 w/ +12s injected CPU stall):** mode=`{qbp['execution_mode']}`, max_backlog=`{qbp['max_backlog_sec']}s`, dropped_backlog_windows=`{qbp['dropped_backlog_windows']}`, commits=`{qbp['committed_segments']}`, norm_WER=`{qbp['m234_preview_norm_wer']:.4f}`",
        "",
    ])
    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    print(f"[PostCallBench] Done -> {OUT_JSON} & {OUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

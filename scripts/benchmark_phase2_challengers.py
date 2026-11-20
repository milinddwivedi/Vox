"""
Phase 2 Challenger Sequential Benchmark Harness for VOX Speech Intelligence.

Evaluates GPU/CPU ASR challengers sequentially on FULL M#234 (90.15s) and FULL M#235 (1129.00s):
  - `whisper-small`           (continuous-stream live simulation + full offline finalization)
  - `whisper-medium`          (continuous-stream live simulation + full offline finalization)
  - `whisper-large-v3`        (full 32-layer offline finalization, no 300s cutoff)
  - `qwen3-asr-0.6b`          (Qwen/Qwen3-ASR-0.6B-hf CUDA FP16 full M#234 & M#235 + chunk latency)
  - `qwen3-asr-0.6b-hinglish` (moorlee/qwen3-asr-0.6b-hinglish CUDA FP16 full M#234 & M#235 + chunk latency)

Strictly read-only on `data/voxlocal.db` (?mode=ro), `data/config.json`, and all original recordings.
Enforces `--min-free-vram-mb` before loading GPU challengers so results are never distorted by
concurrent tray `VoxLocal.exe` VRAM contention. Never kills external processes.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"
for p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from benchmark_speech_intelligence import (
    BENCH_DIR,
    _InMemoryReplayRecorder,
    _ReadOnlyVocabProxy,
    compute_segment_metrics,
    compute_target_db_rows_sha256,
    get_process_rss_mb,
    query_gpu_vram,
    read_only_db_state,
    run_offline_finalization,
    snapshot_protected_files,
)
from score_ground_truth import score_predictions_against_reference
from voxlocal.audio.devices import TARGET_SAMPLE_RATE, normalize_speech_audio
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.streaming_transcriber import LiveMeetingStreamingTranscriber
from voxlocal.engine.transcriber import LocalWhisperTranscriber, load_wav_as_16k_f32

OUT_DIR = BENCH_DIR / "phase2_challengers"
CHALLENGER_MODELS_DIR = ROOT_DIR / "data" / "models" / "challengers"
REPORT_PATH = OUT_DIR / "phase2_challenger_report.json"

ALL_MODELS = (
    "whisper-small",
    "whisper-medium",
    "whisper-large-v3-turbo",
    "whisper-large-v3",
    "qwen3-asr-0.6b",
    "qwen3-asr-0.6b-hinglish",
)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sequential read-only Phase 2 challenger benchmark across full M#234 and M#235."
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=list(ALL_MODELS),
        choices=list(ALL_MODELS),
        help="Challenger models to run sequentially (default: all 6).",
    )
    parser.add_argument(
        "--min-free-vram-mb",
        type=int,
        default=2400,
        help="Minimum free GPU VRAM (MB) required before GPU model runs (default: 2400).",
    )
    parser.add_argument(
        "--allow-contended-vram",
        action="store_true",
        help="Bypass the free VRAM guard (only for diagnostic testing).",
    )
    parser.add_argument(
        "--score-if-verified",
        action="store_true",
        help="Run score_ground_truth.py against M234/M235 reference JSONs after each model.",
    )
    parser.add_argument(
        "--check-env",
        action="store_true",
        help="Verify imports, GPU VRAM, and protected files read-only without running inference.",
    )
    return parser.parse_args(argv)


def _gpu_used_free() -> Tuple[Optional[int], Optional[int]]:
    g = query_gpu_vram()
    if "error" in g:
        return None, None
    return int(g.get("used_mb", 0)), int(g.get("free_mb", 0))


def _release_vram() -> None:
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
    except Exception:
        pass


def run_continuous_live_simulation(
    transcriber: LocalWhisperTranscriber,
    mic_audio: np.ndarray,
    sys_audio: np.ndarray,
    vocab: List[Dict[str, Any]],
    meeting_id: int,
    remote_name: str,
    language: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    True continuous-stream virtual real-time clock simulation over FULL meeting audio.

    On each polling tick (0.15s), measures actual compute time `infer_wall = perf_counter() - t0`
    and advances virtual wall clock by `max(0.15, infer_wall)` so queue depth, backlog,
    chunk drops (>25s buffer cap), delayed ticks (>0.15s), revisions, and P50/P95
    speech-to-display latency reflect true real-time behavior without idle sleep waste.
    """
    total_dur = max(mic_audio.size, sys_audio.size) / float(TARGET_SAMPLE_RATE)
    rec = _InMemoryReplayRecorder(mic_audio, sys_audio, time_offset=0.0)
    proxy_db = _ReadOnlyVocabProxy(vocab)

    partials: List[Dict[str, Any]] = []
    confirmed: List[Dict[str, Any]] = []
    virtual_wall_sec = 0.0
    step_start_perf = [time.perf_counter()]
    partial_latencies_ms: List[float] = []
    turn_start_to_partial_ms: List[float] = []
    confirmed_latencies_ms: List[float] = []
    turn_start_to_confirmed_ms: List[float] = []

    def _on_partial(_mid: int, seg: Dict[str, Any]) -> None:
        d = dict(seg)
        partials.append(d)
        emit_virtual = virtual_wall_sec + (time.perf_counter() - step_start_perf[0])
        lat = max(0.0, (emit_virtual - rec.elapsed_seconds) * 1000.0)
        partial_latencies_ms.append(lat)
        turn_start_to_partial_ms.append(max(0.0, (emit_virtual - float(d.get("start_time", rec.elapsed_seconds))) * 1000.0))

    def _on_confirmed(_mid: int, seg: Dict[str, Any]) -> None:
        d = dict(seg)
        confirmed.append(d)
        emit_virtual = virtual_wall_sec + (time.perf_counter() - step_start_perf[0])
        end_t = float(d.get("end_time", rec.elapsed_seconds))
        start_t = float(d.get("start_time", end_t))
        confirmed_latencies_ms.append(max(0.0, (emit_virtual - end_t) * 1000.0))
        turn_start_to_confirmed_ms.append(max(0.0, (emit_virtual - start_t) * 1000.0))

    streamer = LiveMeetingStreamingTranscriber(
        transcriber=transcriber,
        db=proxy_db,
        on_partial=_on_partial,
        on_confirmed=_on_confirmed,
    )
    streamer._meeting_id = meeting_id
    streamer._recorder = rec
    streamer._language = language
    streamer._remote_speaker_label = remote_name
    streamer._current_speaker = remote_name
    streamer._audio_cursor_sec = 0.0
    streamer._active = True
    streamer._stop_event.clear()

    backlogs_sec: List[float] = []
    queue_depths: List[int] = []
    delayed_chunks = 0
    dropped_chunks = 0
    peak_vram_used_mb = 0
    peak_ram_mb = get_process_rss_mb()

    t0_all = time.perf_counter()
    cpu0_all = time.process_time()
    step_count = 0
    last_cursor = float(streamer._audio_cursor_sec)
    stalled_after_end_sec = 0.0

    while streamer._audio_cursor_sec < total_dur - 0.35 and virtual_wall_sec <= total_dur + 3600.0:
        step_count += 1
        rec.elapsed_seconds = min(total_dur, virtual_wall_sec)

        ts0 = time.perf_counter()
        step_start_perf[0] = ts0
        streamer._step()
        infer_wall = time.perf_counter() - ts0

        if infer_wall > 0.15:
            delayed_chunks += 1
        virtual_wall_sec += max(0.15, infer_wall)

        post_backlog = max(0.0, min(total_dur, virtual_wall_sec) - float(streamer._audio_cursor_sec))
        backlogs_sec.append(post_backlog)
        queue_depths.append(int(math.ceil(post_backlog / 0.15)))
        if post_backlog > 25.0:
            dropped_chunks += 1

        if step_count % 50 == 0:
            used_mb, _ = _gpu_used_free()
            if used_mb is not None:
                peak_vram_used_mb = max(peak_vram_used_mb, used_mb)
            peak_ram_mb = max(peak_ram_mb, get_process_rss_mb())
        if step_count % 500 == 0 and total_dur > 200.0:
            print(
                f"  [LiveTick {step_count}] cursor={streamer._audio_cursor_sec:.1f}/{total_dur:.1f}s "
                f"vwall={virtual_wall_sec:.1f}s backlog={post_backlog:.2f}s confs={len(confirmed)} parts={len(partials)}",
                flush=True,
            )

        cur_cursor = float(streamer._audio_cursor_sec)
        if cur_cursor > last_cursor + 0.01:
            last_cursor = cur_cursor
            stalled_after_end_sec = 0.0
        elif virtual_wall_sec >= total_dur:
            stalled_after_end_sec += max(0.15, infer_wall)
            if stalled_after_end_sec >= 3.5:
                break

    rec.elapsed_seconds = total_dur
    ts_stop0 = time.perf_counter()
    step_start_perf[0] = ts_stop0
    streamer.stop()
    virtual_wall_sec += max(0.0, time.perf_counter() - ts_stop0)

    compute_wall = time.perf_counter() - t0_all
    cpu_sec = time.process_time() - cpu0_all
    cpu_cores = max(1, os.cpu_count() or 1)
    used_mb, _ = _gpu_used_free()
    if used_mb is not None:
        peak_vram_used_mb = max(peak_vram_used_mb, used_mb)
    peak_ram_mb = max(peak_ram_mb, get_process_rss_mb())

    all_lats = confirmed_latencies_ms + partial_latencies_ms
    lat_arr = np.array(all_lats, dtype=np.float64) if all_lats else np.zeros(1)
    part_lat_arr = np.array(partial_latencies_ms, dtype=np.float64) if partial_latencies_ms else np.zeros(1)
    conf_lat_arr = np.array(confirmed_latencies_ms, dtype=np.float64) if confirmed_latencies_ms else np.zeros(1)
    turn_part_arr = np.array(turn_start_to_partial_ms, dtype=np.float64) if turn_start_to_partial_ms else np.zeros(1)
    turn_conf_arr = np.array(turn_start_to_confirmed_ms, dtype=np.float64) if turn_start_to_confirmed_ms else np.zeros(1)
    bl_arr = np.array(backlogs_sec, dtype=np.float64) if backlogs_sec else np.zeros(1)
    qd_arr = np.array(queue_depths, dtype=np.float64) if queue_depths else np.zeros(1)

    metrics = compute_segment_metrics(confirmed, total_dur)
    summary = {
        "mode": "continuous_stream_full_recording",
        "duration_sec": round(total_dur, 2),
        "compute_wall_time_sec": round(compute_wall, 2),
        "simulated_stream_wall_sec": round(virtual_wall_sec, 2),
        "rtf": round(compute_wall / max(0.01, total_dur), 4),
        "cpu_time_sec": round(cpu_sec, 2),
        "avg_cpu_pct": round((cpu_sec / max(0.01, compute_wall)) * 100.0 / cpu_cores, 1),
        "peak_ram_rss_mb": round(peak_ram_mb, 1),
        "peak_vram_used_mb": peak_vram_used_mb,
        "speech_to_display_latency_ms": {
            "partial_p50_ms": round(float(np.median(part_lat_arr)), 1),
            "partial_p95_ms": round(float(np.percentile(part_lat_arr, 95)), 1),
            "turn_start_to_partial_p50_ms": round(float(np.median(turn_part_arr)), 1),
            "confirmed_p50_ms": round(float(np.median(conf_lat_arr)), 1),
            "confirmed_p95_ms": round(float(np.percentile(conf_lat_arr, 95)), 1),
            "confirmed_max_ms": round(float(np.max(conf_lat_arr)), 1),
            "turn_start_to_confirmed_p50_ms": round(float(np.median(turn_conf_arr)), 1),
            "all_events_p50_ms": round(float(np.median(lat_arr)), 1),
            "all_events_p95_ms": round(float(np.percentile(lat_arr, 95)), 1),
        },
        "audio_backlog_sec": {
            "avg_sec": round(float(np.mean(bl_arr)), 3),
            "p95_sec": round(float(np.percentile(bl_arr, 95)), 3),
            "max_sec": round(float(np.max(bl_arr)), 3),
        },
        "processing_queue_depth": {
            "avg": round(float(np.mean(qd_arr)), 1),
            "max": int(np.max(qd_arr)),
        },
        "delayed_chunks": delayed_chunks,
        "dropped_chunks": dropped_chunks,
        "transcript_revisions": len(partials),
        "metrics": metrics,
    }
    return summary, confirmed


def _load_qwen3_asr_and_processor(repo_id: str, local_dir: Path):
    import torch
    from huggingface_hub import hf_hub_download, snapshot_download
    from safetensors.torch import load_file
    from transformers import AutoProcessor, GenerationConfig, Qwen3ASRConfig, Qwen3ASRForConditionalGeneration

    local_dir.mkdir(parents=True, exist_ok=True)
    snap_path = Path(snapshot_download(repo_id=repo_id, local_dir=str(local_dir)))
    cfg_raw = json.loads((snap_path / "config.json").read_text(encoding="utf-8"))

    if "thinker_config" not in cfg_raw:
        # Native HF format (Qwen/Qwen3-ASR-0.6B-hf)
        processor = AutoProcessor.from_pretrained(str(snap_path))
        model = Qwen3ASRForConditionalGeneration.from_pretrained(
            str(snap_path),
            dtype=torch.float16,
        )
        model = model.to(device="cuda", dtype=torch.float16)
        model.eval()
        return model, processor

    # Legacy thinker_config format (e.g., moorlee/qwen3-asr-0.6b-hinglish):
    # Cleanly map config & 611 tensors into transformers' native Qwen3ASRForConditionalGeneration.
    hf_ref_dir = CHALLENGER_MODELS_DIR / "qwen3-asr-0.6b-hf-proc"
    hf_ref_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("processor_config.json", "chat_template.jinja", "tokenizer_config.json", "tokenizer.json"):
        hf_hub_download(repo_id="Qwen/Qwen3-ASR-0.6B-hf", filename=fname, local_dir=str(hf_ref_dir))
    processor = AutoProcessor.from_pretrained(str(hf_ref_dir))

    tc = cfg_raw["thinker_config"]
    hf_ref = Qwen3ASRConfig()
    valid_audio_keys = set(hf_ref.audio_config.to_dict().keys())
    valid_text_keys = set(hf_ref.text_config.to_dict().keys())

    ac = {k: v for k, v in tc.get("audio_config", {}).items() if k in valid_audio_keys and v is not None}
    ac["model_type"] = "qwen3_asr_encoder"

    raw_text_cfg = dict(tc.get("text_config", {}))
    rope_theta = float(raw_text_cfg.get("rope_theta", 1000000))
    tcfg = {k: v for k, v in raw_text_cfg.items() if k in valid_text_keys and v is not None}
    tcfg["model_type"] = "qwen3"
    tcfg["rope_parameters"] = {"rope_theta": rope_theta, "rope_type": "default"}

    ts_tok = tc.get("timestamp_token_id")
    q_cfg = Qwen3ASRConfig(
        audio_config=ac,
        text_config=tcfg,
        audio_token_id=int(tc.get("audio_token_id", 151676)),
        timestamp_token_id=int(ts_tok) if ts_tok is not None else 151705,
        tie_word_embeddings=bool(raw_text_cfg.get("tie_word_embeddings", True)),
    )
    model = Qwen3ASRForConditionalGeneration(q_cfg)
    raw_sd = load_file(str(snap_path / "model.safetensors"), device="cpu")
    mapped_sd: Dict[str, Any] = {}
    for k, v in raw_sd.items():
        new_k = k
        if new_k.startswith("thinker.audio_tower.proj1."):
            new_k = "model.multi_modal_projector.linear_1." + new_k[len("thinker.audio_tower.proj1."):]
        elif new_k.startswith("thinker.audio_tower.proj2."):
            new_k = "model.multi_modal_projector.linear_2." + new_k[len("thinker.audio_tower.proj2."):]
        elif new_k.startswith("thinker.audio_tower."):
            new_k = "model.audio_tower." + new_k[len("thinker.audio_tower."):]
        elif new_k.startswith("thinker.model."):
            new_k = "model.language_model." + new_k[len("thinker.model."):]
        elif new_k.startswith("thinker.lm_head."):
            new_k = "lm_head." + new_k[len("thinker.lm_head."):]
        mapped_sd[new_k] = v.to(dtype=torch.float16)

    if "lm_head.weight" not in mapped_sd and "model.language_model.embed_tokens.weight" in mapped_sd:
        mapped_sd["lm_head.weight"] = mapped_sd["model.language_model.embed_tokens.weight"]

    missing, unexpected = model.load_state_dict(mapped_sd, strict=False)
    real_missing = [k for k in missing if k != "lm_head.weight"]
    if real_missing or unexpected:
        raise RuntimeError(
            f"State dict alignment mismatch for {repo_id}: missing={real_missing[:5]} unexpected={unexpected[:5]}"
        )
    gen_cfg_path = snap_path / "generation_config.json"
    if gen_cfg_path.exists():
        model.generation_config = GenerationConfig.from_pretrained(str(snap_path))
    model.generation_config.eos_token_id = [151643, 151645]
    if getattr(model.generation_config, "pad_token_id", None) is None:
        model.generation_config.pad_token_id = 151643
    model = model.to(device="cuda", dtype=torch.float16)
    model.eval()
    return model, processor


def run_qwen3_challenger(
    model_key: str,
    repo_id: str,
    local_dir: Path,
    meetings: Dict[int, Dict[str, Any]],
    vocab: List[Dict[str, Any]],
    score_if_verified: bool,
) -> Dict[str, Any]:
    import torch

    vram_before_mb, _ = _gpu_used_free()
    t_load0 = time.perf_counter()
    model, processor = _load_qwen3_asr_and_processor(repo_id, local_dir)
    load_sec = time.perf_counter() - t_load0
    used_after_load, free_after_load = _gpu_used_free()
    print(
        f"[Phase2] Loaded {model_key} ({repo_id}) on CUDA FP16 in {load_sec:.2f}s | VRAM={used_after_load}MB used / {free_after_load}MB free",
        flush=True,
    )

    hotword_prompt = "WFX, PLM, ERP, HubSpot, VOX, Teams, Postman, UPC, Daily Scrum, Hemanth, Rumjhum, Vikas, Abhilash"
    use_prompt = None if "hinglish" in model_key else hotword_prompt
    vad_p = dict(threshold=0.35, min_silence_duration_ms=500, speech_pad_ms=250)
    cpu_cores = max(1, os.cpu_count() or 1)
    peak_vram_overall = used_after_load or 0
    model_entry: Dict[str, Any] = {
        "status": "ok",
        "repo_id": repo_id,
        "hotword_prompt_used": bool(use_prompt),
        "load_sec": round(load_sec, 2),
        "vram_before_load_mb": vram_before_mb,
        "vram_after_load_mb": used_after_load,
        "meetings": {},
    }

    try:
        for mid in (234, 235):
            m = meetings[mid]
            dur = float(m["duration_sec"])
            mic_path = LocalWhisperTranscriber._prefer_raw_wav_path(m["mic_audio_path"])
            sys_path = LocalWhisperTranscriber._prefer_raw_wav_path(m["sys_audio_path"])
            mic_aud = load_wav_as_16k_f32(mic_path)
            sys_aud = load_wav_as_16k_f32(sys_path)

            all_segs: List[Dict[str, Any]] = []
            chunk_records: List[Tuple[float, float, float]] = []
            chunk_lat_ms: List[float] = []
            peak_vram = used_after_load or 0
            peak_ram = get_process_rss_mb()

            t0 = time.perf_counter()
            c0 = time.process_time()
            chunk_idx = 0

            for trk, spk, aud in (("mic", "You (Mic)", mic_aud), ("system", "Speaker 1", sys_aud)):
                spans = LocalWhisperTranscriber._extract_vad_chunk_spans(
                    aud, vad_p, max_chunk_sec=22.0, merge_gap_sec=0.65, pad_sec=0.20
                )
                if spans is None:
                    spans = [(0, aud.size)]
                for s_i, e_i in spans:
                    sl = aud[s_i:e_i]
                    if sl.size < int(0.25 * TARGET_SAMPLE_RATE) or float(np.max(np.abs(sl))) < 0.0005:
                        continue
                    chunk_idx += 1
                    s_sec = round(s_i / float(TARGET_SAMPLE_RATE), 2)
                    e_sec = round(e_i / float(TARGET_SAMPLE_RATE), 2)
                    norm_sl = normalize_speech_audio(sl)
                    tc0 = time.perf_counter()
                    inputs = processor.apply_transcription_request(
                        audio=norm_sl,
                        language=None,
                        prompt=use_prompt,
                        tokenize=True,
                    )
                    inputs = inputs.to("cuda")
                    if "input_features" in inputs:
                        inputs["input_features"] = inputs["input_features"].to(dtype=torch.float16)
                    in_len = inputs["input_ids"].shape[1]
                    with torch.inference_mode():
                        gen_ids = model.generate(**inputs, max_new_tokens=320, do_sample=False)
                    raw_dec = processor.decode(gen_ids[0][in_len:], skip_special_tokens=True)
                    del inputs, gen_ids
                    infer_s = time.perf_counter() - tc0
                    parsed = processor.parse_output(raw_dec)
                    raw_txt = str(parsed.get("transcription") if isinstance(parsed, dict) else raw_dec).strip()
                    det_lang = parsed.get("language")
                    chunk_lat_ms.append(infer_s * 1000.0)
                    chunk_records.append((s_sec, e_sec, infer_s))

                    if chunk_idx % 15 == 0:
                        torch.cuda.empty_cache()

                    u_mb, _ = _gpu_used_free()
                    if u_mb is not None:
                        peak_vram = max(peak_vram, u_mb)
                        peak_vram_overall = max(peak_vram_overall, u_mb)
                    peak_ram = max(peak_ram, get_process_rss_mb())

                    if not raw_txt:
                        continue
                    norm_txt = DictationProcessor.process(
                        raw_txt,
                        vocabulary=vocab,
                        cleanup_enabled=True,
                        spoken_punctuation=False,
                        output_script="roman",
                        mode="cleaned",
                    ).strip()
                    all_segs.append(
                        {
                            "start_time": s_sec,
                            "end_time": e_sec,
                            "source_track": trk,
                            "speaker": spk,
                            "detected_language": det_lang,
                            "raw_text": raw_txt,
                            "text": norm_txt or raw_txt,
                        }
                    )

            all_segs.sort(key=lambda s: (float(s["start_time"]), float(s["end_time"])))
            for idx, s in enumerate(all_segs):
                s["segment_index"] = idx

            # Virtual-clock chunked live queue simulation across full meeting timeline
            chunk_records.sort(key=lambda x: (x[1], x[0]))
            q_ready = 0.0
            q_backlogs: List[float] = []
            q_s2d_ms: List[float] = []
            q_delayed = 0
            q_dropped = 0
            for c_s, c_e, c_inf in chunk_records:
                c_dur = max(0.25, c_e - c_s)
                backlog = max(0.0, q_ready - c_e)
                q_backlogs.append(backlog)
                if backlog > 25.0:
                    q_dropped += 1
                if c_inf > c_dur:
                    q_delayed += 1
                start_exec = max(q_ready, c_e)
                finish_exec = start_exec + c_inf
                q_ready = finish_exec
                q_s2d_ms.append((finish_exec - c_e) * 1000.0)

            wall = time.perf_counter() - t0
            cpu_s = time.process_time() - c0
            pred_path = OUT_DIR / f"{model_key}_offline_M{mid}.json"
            pred_path.write_text(
                json.dumps({"meeting_id": mid, "title": m["title"], "model": model_key, "segments": all_segs}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            lat_arr = np.array(chunk_lat_ms, dtype=np.float64) if chunk_lat_ms else np.zeros(1)
            q_lat_arr = np.array(q_s2d_ms, dtype=np.float64) if q_s2d_ms else np.zeros(1)
            q_bl_arr = np.array(q_backlogs, dtype=np.float64) if q_backlogs else np.zeros(1)
            entry: Dict[str, Any] = {
                "title": m["title"],
                "duration_sec": dur,
                "offline_full": {
                    "wall_time_sec": round(wall, 2),
                    "rtf": round(wall / max(0.01, dur), 4),
                    "cpu_time_sec": round(cpu_s, 2),
                    "avg_cpu_pct": round((cpu_s / max(0.01, wall)) * 100.0 / cpu_cores, 1),
                    "peak_ram_rss_mb": round(peak_ram, 1),
                    "peak_vram_used_mb": peak_vram,
                    "chunk_count": len(chunk_lat_ms),
                    "chunk_latency_ms": {
                        "p50_ms": round(float(np.median(lat_arr)), 1),
                        "p95_ms": round(float(np.percentile(lat_arr, 95)), 1),
                        "max_ms": round(float(np.max(lat_arr)), 1),
                    },
                    "chunked_live_sim": {
                        "effective_live_rtf": round(sum(c[2] for c in chunk_records) / max(0.01, dur), 4),
                        "speech_to_display_p50_ms": round(float(np.median(q_lat_arr)), 1),
                        "speech_to_display_p95_ms": round(float(np.percentile(q_lat_arr, 95)), 1),
                        "speech_to_display_max_ms": round(float(np.max(q_lat_arr)), 1),
                        "backlog_max_sec": round(float(np.max(q_bl_arr)), 2),
                        "delayed_chunks": q_delayed,
                        "dropped_chunks": q_dropped,
                    },
                    "metrics": compute_segment_metrics(all_segs, dur),
                    "prediction_path": str(pred_path),
                    "sample_excerpts": all_segs[:3],
                },
            }
            if score_if_verified:
                ref_file = BENCH_DIR / ("M234_hemanth_reference_draft.json" if mid == 234 else "M235_rumjhum_reference_draft.json")
                ref_data = json.loads(ref_file.read_text(encoding="utf-8"))
                entry["offline_scores"] = score_predictions_against_reference(ref_data, all_segs)
            model_entry["meetings"][str(mid)] = entry
            print(
                f"[Phase2] {model_key} M#{mid}: wall={wall:.2f}s rtf={wall/dur:.4f} chunk_p50={entry['offline_full']['chunk_latency_ms']['p50_ms']}ms live_backlog_max={entry['offline_full']['chunked_live_sim']['backlog_max_sec']}s segs={len(all_segs)} vram_peak={peak_vram}MB",
                flush=True,
            )
    finally:
        del model
        _release_vram()
        vram_after_mb, _ = _gpu_used_free()
        model_entry["peak_vram_used_mb"] = peak_vram_overall
        model_entry["vram_after_unload_mb"] = vram_after_mb
        model_entry["vram_recovered_delta_mb"] = (
            (vram_after_mb - vram_before_mb)
            if (vram_after_mb is not None and vram_before_mb is not None)
            else None
        )
    return model_entry


CHALLENGER_WHISPER_REPOS = {
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
}


def run_whisper_challenger(
    model_key: str,
    whisper_size: str,
    run_live: bool,
    meetings: Dict[int, Dict[str, Any]],
    vocab: List[Dict[str, Any]],
    score_if_verified: bool,
) -> Dict[str, Any]:
    from faster_whisper import WhisperModel
    from huggingface_hub import snapshot_download

    with (ROOT_DIR / "data" / "config.json").open("r", encoding="utf-8") as cf:
        cfg = json.load(cf)

    vram_before_mb, _ = _gpu_used_free()
    transcriber = LocalWhisperTranscriber(
        models_dir=cfg.get("models_dir"),
        model_name=whisper_size,
        compute_device="cuda",
        compute_type="int8",
        cpu_threads=int(cfg.get("cpu_threads", 8)),
    )
    t_load0 = time.perf_counter()
    if whisper_size in CHALLENGER_WHISPER_REPOS:
        target_dir = CHALLENGER_MODELS_DIR / f"whisper-{whisper_size}"
        target_dir.mkdir(parents=True, exist_ok=True)
        snap_dir = snapshot_download(
            repo_id=CHALLENGER_WHISPER_REPOS[whisper_size],
            local_dir=str(target_dir),
        )
        ct2_model = WhisperModel(str(snap_dir), device="cuda", compute_type="int8")
        dummy = np.zeros(8000, dtype=np.float32)
        warm_segs, _ = ct2_model.transcribe(dummy, beam_size=1)
        list(warm_segs)
        transcriber._model = ct2_model
        transcriber._loaded_model_name = whisper_size
        transcriber.model_name = whisper_size
        transcriber._active_device = "cuda"
    else:
        ok, msg = transcriber.ensure_loaded()
        if not ok:
            raise RuntimeError(f"Failed to load {whisper_size}: {msg}")
        if transcriber._loaded_model_name != whisper_size:
            raise RuntimeError(
                f"Expected '{whisper_size}' but ensure_loaded() loaded '{transcriber._loaded_model_name}'"
            )

    locked_model_id = id(transcriber._model)
    transcriber.is_model_cached = lambda *a, **kw: True  # type: ignore[method-assign]
    transcriber.ensure_loaded = lambda *a, **kw: (True, f"Locked challenger {whisper_size}")  # type: ignore[method-assign]

    load_sec = time.perf_counter() - t_load0
    used_mb, free_mb = _gpu_used_free()
    peak_vram_overall = used_mb or 0
    print(
        f"[Phase2] Loaded faster-whisper {whisper_size} ({transcriber._active_device}) in {load_sec:.2f}s | VRAM={used_mb}MB used / {free_mb}MB free",
        flush=True,
    )

    model_entry: Dict[str, Any] = {
        "status": "ok",
        "whisper_model": whisper_size,
        "load_sec": round(load_sec, 2),
        "vram_before_load_mb": vram_before_mb,
        "vram_after_load_mb": used_mb,
        "meetings": {},
    }

    try:
        for mid in (234, 235):
            m = meetings[mid]
            dur = float(m["duration_sec"])
            mic_path = LocalWhisperTranscriber._prefer_raw_wav_path(m["mic_audio_path"])
            sys_path = LocalWhisperTranscriber._prefer_raw_wav_path(m["sys_audio_path"])
            entry: Dict[str, Any] = {"title": m["title"], "duration_sec": dur}

            if run_live:
                mic_aud = load_wav_as_16k_f32(mic_path)
                sys_aud = load_wav_as_16k_f32(sys_path)
                live_summary, live_segs = run_continuous_live_simulation(
                    transcriber, mic_aud, sys_aud, vocab, mid, m["title"], "en+hi"
                )
                peak_vram_overall = max(peak_vram_overall, int(live_summary.get("peak_vram_used_mb", 0) or 0))
                live_path = OUT_DIR / f"{model_key}_live_M{mid}.json"
                live_path.write_text(
                    json.dumps({"meeting_id": mid, "title": m["title"], "model": model_key, "mode": "live", "segments": live_segs}, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                live_summary["prediction_path"] = str(live_path)
                entry["live_continuous"] = live_summary
                print(
                    f"[Phase2] {model_key} LIVE M#{mid}: rtf={live_summary['rtf']} conf_p50={live_summary['speech_to_display_latency_ms']['confirmed_p50_ms']}ms conf_p95={live_summary['speech_to_display_latency_ms']['confirmed_p95_ms']}ms backlog_max={live_summary['audio_backlog_sec']['max_sec']}s dropped={live_summary['dropped_chunks']} segs={len(live_segs)}",
                    flush=True,
                )

            final_res = run_offline_finalization(
                transcriber,
                m["mic_audio_path"],
                m["sys_audio_path"],
                m["audio_path"],
                vocab,
                m["title"],
                "en+hi",
                dur,
            )
            off_vram = int((final_res.get("gpu") or {}).get("used_mb", 0) or 0)
            peak_vram_overall = max(peak_vram_overall, off_vram)
            off_segs = final_res.pop("segments")
            off_path = OUT_DIR / f"{model_key}_offline_M{mid}.json"
            off_path.write_text(
                json.dumps({"meeting_id": mid, "title": m["title"], "model": model_key, "mode": "offline", "segments": off_segs}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            final_res["prediction_path"] = str(off_path)
            entry["offline_full"] = final_res

            if score_if_verified:
                ref_file = BENCH_DIR / ("M234_hemanth_reference_draft.json" if mid == 234 else "M235_rumjhum_reference_draft.json")
                ref_data = json.loads(ref_file.read_text(encoding="utf-8"))
                entry["offline_scores"] = score_predictions_against_reference(ref_data, off_segs)
                if run_live and "live_continuous" in entry:
                    entry["live_scores"] = score_predictions_against_reference(ref_data, live_segs)

            model_entry["meetings"][str(mid)] = entry
            assert transcriber._loaded_model_name == whisper_size and id(transcriber._model) == locked_model_id, (
                f"Model drifted during M#{mid}: expected {whisper_size}, got {transcriber._loaded_model_name}"
            )
            print(
                f"[Phase2] {model_key} OFFLINE M#{mid}: wall={final_res['wall_time_sec']}s rtf={final_res['rtf']} segs={final_res['metrics']['segment_count']} vram={off_vram}MB",
                flush=True,
            )
    finally:
        transcriber.unload_model()
        _release_vram()
        vram_after_mb, _ = _gpu_used_free()
        model_entry["peak_vram_used_mb"] = peak_vram_overall
        model_entry["vram_after_unload_mb"] = vram_after_mb
        model_entry["vram_recovered_delta_mb"] = (
            (vram_after_mb - vram_before_mb)
            if (vram_after_mb is not None and vram_before_mb is not None)
            else None
        )
    return model_entry


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    meetings, vocab = read_only_db_state()
    meeting_dirs = [Path(meetings[mid]["audio_path"]).parent for mid in (234, 235)]
    pre_snap = snapshot_protected_files(meeting_dirs)
    pre_db_rows_sha = compute_target_db_rows_sha256()
    used_mb, free_mb = _gpu_used_free()

    if args.check_env:
        import torch
        import transformers
        print(
            json.dumps(
                {
                    "status": "env_ok",
                    "torch": torch.__version__,
                    "cuda_available": torch.cuda.is_available(),
                    "transformers": transformers.__version__,
                    "gpu_used_mb": used_mb,
                    "gpu_free_mb": free_mb,
                    "min_free_vram_mb": args.min_free_vram_mb,
                    "protected_files": len(pre_snap),
                },
                indent=2,
            )
        )
        return 0

    if free_mb is not None and free_mb < int(args.min_free_vram_mb) and not args.allow_contended_vram:
        print(
            f"[Phase2] VRAM GUARD STOP: Free GPU VRAM is {free_mb} MB (< {args.min_free_vram_mb} MB required). "
            f"Close the running tray VOX app cleanly via 'Exit VOX' before running GPU benchmarks.",
            file=sys.stderr,
            flush=True,
        )
        return 2

    if REPORT_PATH.exists():
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    else:
        report = {"challengers": {}}

    report["last_updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    for model_key in args.models:
        _release_vram()
        v_pre, _ = _gpu_used_free()
        try:
            if model_key == "whisper-small":
                res = run_whisper_challenger("whisper-small", "small", True, meetings, vocab, args.score_if_verified)
            elif model_key == "whisper-medium":
                res = run_whisper_challenger("whisper-medium", "medium", True, meetings, vocab, args.score_if_verified)
            elif model_key == "whisper-large-v3-turbo":
                res = run_whisper_challenger("whisper-large-v3-turbo", "large-v3-turbo", True, meetings, vocab, args.score_if_verified)
            elif model_key == "whisper-large-v3":
                res = run_whisper_challenger("whisper-large-v3", "large-v3", False, meetings, vocab, args.score_if_verified)
            elif model_key == "qwen3-asr-0.6b":
                res = run_qwen3_challenger(
                    "qwen3-asr-0.6b",
                    "Qwen/Qwen3-ASR-0.6B-hf",
                    CHALLENGER_MODELS_DIR / "qwen3-asr-0.6b",
                    meetings,
                    vocab,
                    args.score_if_verified,
                )
            elif model_key == "qwen3-asr-0.6b-hinglish":
                res = run_qwen3_challenger(
                    "qwen3-asr-0.6b-hinglish",
                    "moorlee/qwen3-asr-0.6b-hinglish",
                    CHALLENGER_MODELS_DIR / "qwen3-asr-0.6b-hinglish",
                    meetings,
                    vocab,
                    args.score_if_verified,
                )
            else:
                raise ValueError(f"Unsupported model key: {model_key}")
        except Exception as model_exc:
            _release_vram()
            v_post, v_free_post = _gpu_used_free()
            res = {
                "status": "oom_or_error",
                "error_message": str(model_exc),
                "vram_before_load_mb": v_pre,
                "vram_after_unload_mb": v_post,
                "vram_free_after_error_mb": v_free_post,
                "meetings": {},
            }
            print(f"[Phase2] ERROR/OOM on {model_key}: {model_exc}", file=sys.stderr, flush=True)

        report["challengers"][model_key] = res
        REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    post_snap = snapshot_protected_files(meeting_dirs)
    post_db_rows_sha = compute_target_db_rows_sha256()
    mismatches = [k for k in pre_snap if k not in post_snap or pre_snap[k] != post_snap[k]]
    if pre_db_rows_sha != post_db_rows_sha:
        mismatches.append("DB_TARGET_ROWS")

    report["read_only_integrity"] = {
        "checked_file_count": len(pre_snap),
        "all_recording_and_config_hashes_unchanged": len(mismatches) == 0,
        "target_db_rows_unchanged": pre_db_rows_sha == post_db_rows_sha,
        "mismatches": mismatches,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Phase2] Saved {REPORT_PATH} | protected_intact={len(mismatches) == 0}", flush=True)
    if mismatches:
        raise RuntimeError(f"READ-ONLY INTEGRITY VIOLATION: {mismatches}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

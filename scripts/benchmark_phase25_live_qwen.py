"""
VOX Phase 2.5 — Genuine Continuous-Stream Live Evaluation of Winning Qwen3 Candidate.

Wraps the winning Step 4 A/B candidate (`qwen3-native-guarded`: Qwen/Qwen3-ASR-0.6B-hf
with English/Hindi language-prefix LogitsProcessor + foreign-script mask + speech-gated
VOX hotwords + prompt-echo guard) behind a read-only adapter plugging into VOX's real
`LiveMeetingStreamingTranscriber` (`src/voxlocal/engine/streaming_transcriber.py`).

Measures genuine continuous-stream 0.15s ticks across FULL M#234 (90.15s) & FULL M#235
(1129.00s): partial/confirmed latency P50/P95, backlog growth by quarter (Q1..Q4), dropped
chunks, revisions, memory/VRAM stability, and dual-view View A/B live WER/CER/entities.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"
for p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from benchmark_phase2_challengers import (
    CHALLENGER_MODELS_DIR,
    _gpu_used_free,
    _load_qwen3_asr_and_processor,
    _release_vram,
)
from benchmark_phase25_qwen_ab import (
    VOX_HOTWORDS,
    _decode_chunk_qwen,
    _script_and_echo_audit,
    build_foreign_script_token_ids,
    is_qwen_prompt_echo,
)
from benchmark_speech_intelligence import (
    BENCH_DIR,
    _InMemoryReplayRecorder,
    _ReadOnlyVocabProxy,
    compute_target_db_rows_sha256,
    get_process_rss_mb,
    read_only_db_state,
    snapshot_protected_files,
)
from score_ground_truth import score_predictions_against_reference
from voxlocal.audio.devices import TARGET_SAMPLE_RATE
from voxlocal.engine.streaming_transcriber import LiveMeetingStreamingTranscriber
from voxlocal.engine.transcriber import LocalWhisperTranscriber, load_wav_as_16k_f32

OUT_DIR = BENCH_DIR / "phase2_5_validation"
REPORT_PATH = OUT_DIR / "live_qwen_continuous_report.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for ch in iter(lambda: f.read(65536), b""):
            h.update(ch)
    return h.hexdigest()


class Qwen3StreamingAdapter:
    """
    Non-invasive adapter allowing VOX's real `LiveMeetingStreamingTranscriber` to drive
    `qwen3-native-guarded` (Qwen/Qwen3-ASR-0.6B-hf) without editing `src/`.
    """

    def __init__(self, model: Any, processor: Any, blocked_ids_gpu: Any) -> None:
        self.model = model
        self.processor = processor
        self.blocked_ids_gpu = blocked_ids_gpu
        self._model = None
        self.passes = 0
        self.partial_passes = 0
        self.commit_passes = 0
        self.echo_retries = 0

    def is_model_loaded(self) -> bool:
        return True

    def ensure_loaded(self) -> Tuple[bool, str]:
        return True, "Loaded Qwen3-native-guarded adapter"

    def _run_whisper_pass(self, *args: Any, **kwargs: Any) -> None:
        return None

    def _transcribe_single_encode(
        self,
        norm_audio: np.ndarray,
        *,
        beam_size: int = 1,
        hotwords: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Tuple[List[Any], Any]]:
        import torch

        if norm_audio is None or norm_audio.size < int(TARGET_SAMPLE_RATE * 0.35):
            return [], SimpleNamespace(language="en")

        self.passes += 1
        is_partial = int(beam_size) <= 1
        if is_partial:
            self.partial_passes += 1
            max_toks = 160
        else:
            self.commit_passes += 1
            max_toks = 256

        dur_sec = norm_audio.size / float(TARGET_SAMPLE_RATE)
        norm_rms = float(np.sqrt(np.mean(np.square(norm_audio))))
        use_prompt = VOX_HOTWORDS if (dur_sec >= 1.5 and norm_rms >= 0.02) else None

        raw_txt, det_lang, _, _ = _decode_chunk_qwen(
            self.model,
            self.processor,
            norm_audio,
            variant="native-guarded",
            blocked_ids_gpu=self.blocked_ids_gpu,
            prompt=use_prompt,
            max_new_tokens=max_toks,
            repetition_penalty=1.05,
            no_repeat_ngram_size=5,
        )

        if use_prompt is not None and is_qwen_prompt_echo(raw_txt, use_prompt, dur_sec, norm_rms):
            self.echo_retries += 1
            rt_txt, rt_lang, _, _ = _decode_chunk_qwen(
                self.model,
                self.processor,
                norm_audio,
                variant="native-guarded",
                blocked_ids_gpu=self.blocked_ids_gpu,
                prompt=None,
                max_new_tokens=max_toks,
                repetition_penalty=1.05,
                no_repeat_ngram_size=5,
            )
            if not is_qwen_prompt_echo(rt_txt, use_prompt, dur_sec, norm_rms):
                raw_txt, det_lang = rt_txt, rt_lang
            else:
                raw_txt = ""

        if self.passes % 10 == 0:
            torch.cuda.empty_cache()

        raw_txt = str(raw_txt or "").strip()
        if not raw_txt:
            return [], SimpleNamespace(language=det_lang or "en")

        seg_ns = SimpleNamespace(text=raw_txt, avg_logprob=None, no_speech_prob=0.0)
        return [seg_ns], SimpleNamespace(language=det_lang or "en")


def run_qwen_continuous_live(
    adapter: Qwen3StreamingAdapter,
    mic_audio: np.ndarray,
    sys_audio: np.ndarray,
    vocab: List[Dict[str, Any]],
    meeting_id: int,
    remote_name: str,
    *,
    max_compute_wall_sec: float = 1800.0,
    saturation_backlog_sec: float = 180.0,
    saturation_ticks_limit: int = 200,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
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
        transcriber=adapter,  # type: ignore[arg-type]
        db=proxy_db,
        on_partial=_on_partial,
        on_confirmed=_on_confirmed,
    )
    streamer._meeting_id = meeting_id
    streamer._recorder = rec
    streamer._language = "en+hi"
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

    quarters: Dict[str, Dict[str, Any]] = {
        f"Q{q}": {
            "window_sec": f"{round((q - 1) * 0.25 * total_dur, 1)}-{round(q * 0.25 * total_dur, 1)}s",
            "backlogs": [],
            "peak_vram_mb": 0,
            "peak_ram_rss_mb": 0.0,
            "cursor_end_sec": 0.0,
        }
        for q in (1, 2, 3, 4)
    }

    t0_all = time.perf_counter()
    cpu0_all = time.process_time()
    step_count = 0
    last_cursor = float(streamer._audio_cursor_sec)
    stalled_after_end_sec = 0.0
    high_backlog_ticks = 0
    stream_saturation_aborted = False
    abort_reason: Optional[str] = None
    abort_cursor_sec: Optional[float] = None
    abort_vwall_sec: Optional[float] = None

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

        cur_cursor = float(streamer._audio_cursor_sec)
        post_backlog = max(0.0, min(total_dur, virtual_wall_sec) - cur_cursor)
        backlogs_sec.append(post_backlog)
        queue_depths.append(int(math.ceil(post_backlog / 0.15)))
        if post_backlog > 25.0:
            dropped_chunks += 1

        # Bucket stability telemetry by virtual timeline quarter
        frac = min(0.9999, rec.elapsed_seconds / max(0.01, total_dur))
        qk = f"Q{int(frac * 4.0) + 1}"
        quarters[qk]["backlogs"].append(post_backlog)
        quarters[qk]["cursor_end_sec"] = round(cur_cursor, 2)

        if step_count % 25 == 0:
            used_mb, _ = _gpu_used_free()
            ram_now = get_process_rss_mb()
            if used_mb is not None:
                peak_vram_used_mb = max(peak_vram_used_mb, used_mb)
                quarters[qk]["peak_vram_mb"] = max(quarters[qk]["peak_vram_mb"], used_mb)
            peak_ram_mb = max(peak_ram_mb, ram_now)
            quarters[qk]["peak_ram_rss_mb"] = max(quarters[qk]["peak_ram_rss_mb"], round(ram_now, 1))

        if step_count % 500 == 0 and total_dur > 200.0:
            print(
                f"  [LiveQwenTick {step_count}] cursor={cur_cursor:.1f}/{total_dur:.1f}s "
                f"vwall={virtual_wall_sec:.1f}s compute={time.perf_counter()-t0_all:.1f}s "
                f"backlog={post_backlog:.2f}s confs={len(confirmed)} parts={len(partials)}",
                flush=True,
            )

        if post_backlog > saturation_backlog_sec:
            high_backlog_ticks += 1
        else:
            high_backlog_ticks = 0

        if high_backlog_ticks >= saturation_ticks_limit:
            stream_saturation_aborted = True
            abort_reason = f"runaway_backlog>{saturation_backlog_sec}s_for_{high_backlog_ticks}_ticks"
            abort_cursor_sec = round(cur_cursor, 2)
            abort_vwall_sec = round(virtual_wall_sec, 2)
            break

        if (time.perf_counter() - t0_all) > max_compute_wall_sec and total_dur > 200.0:
            stream_saturation_aborted = True
            abort_reason = f"compute_wall_exceeded_{max_compute_wall_sec}s"
            abort_cursor_sec = round(cur_cursor, 2)
            abort_vwall_sec = round(virtual_wall_sec, 2)
            break

        if cur_cursor > last_cursor + 0.01:
            last_cursor = cur_cursor
            stalled_after_end_sec = 0.0
        elif virtual_wall_sec >= total_dur:
            stalled_after_end_sec += max(0.15, infer_wall)
            if stalled_after_end_sec >= 3.5:
                break

    if not stream_saturation_aborted:
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

    part_arr = np.array(partial_latencies_ms, dtype=np.float64) if partial_latencies_ms else np.zeros(1)
    conf_arr = np.array(confirmed_latencies_ms, dtype=np.float64) if confirmed_latencies_ms else np.zeros(1)
    turn_p_arr = np.array(turn_start_to_partial_ms, dtype=np.float64) if turn_start_to_partial_ms else np.zeros(1)
    turn_c_arr = np.array(turn_start_to_confirmed_ms, dtype=np.float64) if turn_start_to_confirmed_ms else np.zeros(1)
    bl_arr = np.array(backlogs_sec, dtype=np.float64) if backlogs_sec else np.zeros(1)
    qd_arr = np.array(queue_depths, dtype=np.float64) if queue_depths else np.zeros(1)

    quarter_summary: Dict[str, Any] = {}
    for qk, qv in quarters.items():
        qbl = np.array(qv["backlogs"], dtype=np.float64) if qv["backlogs"] else np.zeros(1)
        quarter_summary[qk] = {
            "window_sec": qv["window_sec"],
            "ticks": len(qv["backlogs"]),
            "cursor_end_sec": qv["cursor_end_sec"],
            "avg_backlog_sec": round(float(np.mean(qbl)), 3),
            "max_backlog_sec": round(float(np.max(qbl)), 3),
            "peak_vram_mb": qv["peak_vram_mb"],
            "peak_ram_rss_mb": qv["peak_ram_rss_mb"],
        }

    summary = {
        "mode": "continuous_stream_full_recording",
        "duration_sec": round(total_dur, 2),
        "audio_processed_cursor_sec": round(float(streamer._audio_cursor_sec), 2),
        "stream_saturation_aborted": stream_saturation_aborted,
        "abort_reason": abort_reason,
        "abort_cursor_sec": abort_cursor_sec,
        "abort_vwall_sec": abort_vwall_sec,
        "compute_wall_time_sec": round(compute_wall, 2),
        "simulated_stream_wall_sec": round(virtual_wall_sec, 2),
        "rtf": round(compute_wall / max(0.01, float(streamer._audio_cursor_sec) if stream_saturation_aborted else total_dur), 4),
        "cpu_time_sec": round(cpu_sec, 2),
        "avg_cpu_pct": round((cpu_sec / max(0.01, compute_wall)) * 100.0 / cpu_cores, 1),
        "peak_ram_rss_mb": round(peak_ram_mb, 1),
        "peak_vram_used_mb": peak_vram_used_mb,
        "speech_to_display_latency_ms": {
            "partial_p50_ms": round(float(np.median(part_arr)), 1),
            "partial_p95_ms": round(float(np.percentile(part_arr, 95)), 1),
            "turn_start_to_partial_p50_ms": round(float(np.median(turn_p_arr)), 1),
            "confirmed_p50_ms": round(float(np.median(conf_arr)), 1),
            "confirmed_p95_ms": round(float(np.percentile(conf_arr, 95)), 1),
            "confirmed_max_ms": round(float(np.max(conf_arr)), 1),
            "turn_start_to_confirmed_p50_ms": round(float(np.median(turn_c_arr)), 1),
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
        "confirmed_segments": len(confirmed),
        "avg_revisions_per_confirmed": round(len(partials) / max(1, len(confirmed)), 2),
        "adapter_stats": {
            "passes": adapter.passes,
            "partial_passes": adapter.partial_passes,
            "commit_passes": adapter.commit_passes,
            "echo_retries": adapter.echo_retries,
        },
        "quarter_stability": quarter_summary,
    }
    return summary, confirmed


def main() -> int:
    import torch

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _, free_mb = _gpu_used_free()
    if free_mb is not None and free_mb < 2400:
        print(f"[LiveQwen] VRAM GUARD STOP: free={free_mb} MB < 2400 MB", file=sys.stderr, flush=True)
        return 2

    meetings, vocab = read_only_db_state()
    meeting_dirs = [Path(meetings[mid]["audio_path"]).parent for mid in (234, 235)]
    pre_snap = snapshot_protected_files(meeting_dirs)
    pre_db_sha = compute_target_db_rows_sha256()
    ref_files = {
        234: BENCH_DIR / "M234_hemanth_reference_draft.json",
        235: BENCH_DIR / "M235_rumjhum_reference_draft.json",
    }
    pre_ref_shas = {str(p.resolve()): _sha256_file(p) for p in ref_files.values()}
    ref_drafts = {mid: json.loads(p.read_text(encoding="utf-8")) for mid, p in ref_files.items()}

    _release_vram()
    vram_before, _ = _gpu_used_free()
    t_load0 = time.perf_counter()
    model, processor = _load_qwen3_asr_and_processor(
        "Qwen/Qwen3-ASR-0.6B-hf", CHALLENGER_MODELS_DIR / "qwen3-asr-0.6b"
    )
    load_sec = time.perf_counter() - t_load0
    blocked_ids = build_foreign_script_token_ids(processor.tokenizer)
    blocked_ids_gpu = torch.tensor(blocked_ids, device="cuda", dtype=torch.long)
    vram_after_load, free_after_load = _gpu_used_free()
    print(
        f"[LiveQwen] Loaded qwen3-native-guarded in {load_sec:.2f}s | VRAM={vram_after_load}MB used / {free_after_load}MB free",
        flush=True,
    )

    report: Dict[str, Any] = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "winning_candidate": "qwen3-native-guarded",
        "repo_id": "Qwen/Qwen3-ASR-0.6B-hf",
        "load_sec": round(load_sec, 2),
        "vram_before_load_mb": vram_before,
        "vram_after_load_mb": vram_after_load,
        "decode_config": {
            "partial_max_new_tokens": 160,
            "commit_max_new_tokens": 256,
            "repetition_penalty": 1.05,
            "no_repeat_ngram_size": 5,
            "speech_gated_hotwords": True,
            "foreign_script_mask_tokens": len(blocked_ids),
        },
        "meetings": {},
    }

    try:
        for mid in (234, 235):
            m = meetings[mid]
            mic_path = LocalWhisperTranscriber._prefer_raw_wav_path(m["mic_audio_path"])
            sys_path = LocalWhisperTranscriber._prefer_raw_wav_path(m["sys_audio_path"])
            mic_aud = load_wav_as_16k_f32(mic_path)
            sys_aud = load_wav_as_16k_f32(sys_path)

            adapter = Qwen3StreamingAdapter(model, processor, blocked_ids_gpu)
            live_sum, live_segs = run_qwen_continuous_live(
                adapter, mic_aud, sys_aud, vocab, mid, m["title"]
            )
            pred_path = OUT_DIR / f"live_qwen_continuous_M{mid}.json"
            pred_path.write_text(
                json.dumps(
                    {
                        "meeting_id": mid,
                        "title": m["title"],
                        "model": "qwen3-native-guarded:live_continuous",
                        "segments": live_segs,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            live_sum["prediction_path"] = str(pred_path)
            live_sum["script_and_echo_audit"] = _script_and_echo_audit(live_segs)
            live_sum["scores"] = score_predictions_against_reference(ref_drafts[mid], live_segs)
            report["meetings"][str(mid)] = live_sum

            va = live_sum["scores"]["primary_masked_uncertain"]
            vb = live_sum["scores"]["secondary_unmasked_complete"]
            print(
                f"[LiveQwen M#{mid}] aborted={live_sum['stream_saturation_aborted']} cursor={live_sum['audio_processed_cursor_sec']}/{live_sum['duration_sec']}s "
                f"rtf={live_sum['rtf']} conf_p50={live_sum['speech_to_display_latency_ms']['confirmed_p50_ms']}ms "
                f"backlog_max={live_sum['audio_backlog_sec']['max_sec']}s dropped={live_sum['dropped_chunks']} "
                f"revs={live_sum['transcript_revisions']} segs={len(live_segs)} "
                f"A_WER={va['stream_hinglish_norm_wer']} B_WER={vb['stream_hinglish_norm_wer']} vram_peak={live_sum['peak_vram_used_mb']}MB",
                flush=True,
            )
    finally:
        del model, blocked_ids_gpu
        _release_vram()
        v_after, v_free = _gpu_used_free()
        report["vram_after_unload_mb"] = v_after
        report["vram_free_after_unload_mb"] = v_free

    post_snap = snapshot_protected_files(meeting_dirs)
    post_db_sha = compute_target_db_rows_sha256()
    mismatches = [k for k in pre_snap if k not in post_snap or pre_snap[k] != post_snap[k]]
    if pre_db_sha != post_db_sha:
        mismatches.append("DB_TARGET_ROWS")
    for k, v in pre_ref_shas.items():
        if _sha256_file(Path(k)) != v:
            mismatches.append(k)
    report["read_only_integrity"] = {"all_hashes_unchanged": len(mismatches) == 0, "mismatches": mismatches}
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[LiveQwen] Saved {REPORT_PATH} | protected_intact={len(mismatches) == 0}", flush=True)
    if mismatches:
        raise RuntimeError(f"READ-ONLY INTEGRITY VIOLATION: {mismatches}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

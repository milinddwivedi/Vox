"""
VOX Phase 2.5 — Controlled Final Native Qwen3-ASR vs. Srota Hinglish Offline A/B & Report Builder.

Variants:
  - `--variant native-guarded`: Qwen/Qwen3-ASR-0.6B-hf (CUDA FP16) with verified English/Hindi
    language-prefix LogitsProcessor + foreign-script token mask + speech-gated VOX hotwords +
    prompt-echo guard + repetition penalty/no_repeat_ngram_size.
  - `--variant srota-guarded`: moorlee/qwen3-asr-0.6b-hinglish (CUDA FP16) with foreign-script
    token mask + speech-gated VOX hotwords + prompt-echo guard + short-utterance truncation safeguard.
  - `--variant build-ab-report`: Scores A1, A2, B1, B2 + Whisper Large-v3-Turbo (live/offline) +
    IndicConformer ONNX + baseline_db under View A (primary masked uncertain) & View B (unmasked
    complete), extracting disagreement excerpts alongside verified ground truth.

Run each GPU variant in its own fresh subprocess with `--min-free-vram-mb 2400`.
Strictly read-only on recordings, `data/config.json`, `data/voxlocal.db`, and reference JSONs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
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
from benchmark_speech_intelligence import (
    BENCH_DIR,
    DB_PATH,
    compute_target_db_rows_sha256,
    count_prompt_echoes,
    get_process_rss_mb,
    read_only_db_state,
    snapshot_protected_files,
)
from score_ground_truth import (
    _count_duplication_words,
    load_prediction_segments,
    score_predictions_against_reference,
    tokenize_strict,
)
from voxlocal.audio.devices import TARGET_SAMPLE_RATE
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import (
    LocalWhisperTranscriber,
    is_prompt_contamination_hallucination,
    load_wav_as_16k_f32,
    normalize_speech_audio,
)

OUT_DIR = BENCH_DIR / "phase2_5_validation"
PHASE2_DIR = BENCH_DIR / "phase2_challengers"
AB_REPORT_PATH = OUT_DIR / "qwen_ab_report.json"

VOX_HOTWORDS = (
    "WFX, PLM, ERP, HubSpot, VOX, Teams, Postman, UPC, Remove Programs, System Tray, "
    "Daily Scrum, Account Management, Excel, Windows, Settings, Hemanth, Rumjhum, Vikas, Abhilash"
)
VOX_HOTWORD_TOKENS = {
    t
    for t in tokenize_strict(VOX_HOTWORDS)
    if t and t not in ("and", "the", "of")
}

CJK_RE = re.compile(r"[\u4E00-\u9FFF\u3400-\u4DBF\u3000-\u303F\uFF00-\uFFEF]")
ARAB_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
KANA_HANGUL_CYR_RE = re.compile(r"[\u3040-\u30FF\uAC00-\uD7AF\u0400-\u04FF]")
DEVA_RE = re.compile(r"[\u0900-\u097F]")
FOREIGN_TOKEN_RE = re.compile(
    r"[\u4E00-\u9FFF\u3400-\u4DBF\u3000-\u303F\uFF00-\uFFEF"
    r"\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF"
    r"\u3040-\u30FF\uAC00-\uD7AF\u0400-\u04FF]"
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for ch in iter(lambda: f.read(65536), b""):
            h.update(ch)
    return h.hexdigest()


def build_foreign_script_token_ids(tokenizer: Any, keep_ids: Tuple[int, ...] = (151643, 151645, 151704)) -> List[int]:
    keep_set = set(keep_ids)
    blocked: List[int] = []
    vocab_n = len(tokenizer)
    for tid in range(vocab_n):
        if tid in keep_set:
            continue
        piece = tokenizer.decode([tid], skip_special_tokens=False)
        if piece and FOREIGN_TOKEN_RE.search(piece):
            blocked.append(tid)
    return blocked


def is_qwen_prompt_echo(raw_txt: str, hotwords_str: str, chunk_dur: float, raw_rms: float) -> bool:
    txt = str(raw_txt or "").strip()
    if not txt:
        return False
    if is_prompt_contamination_hallucination(
        txt,
        initial_prompt=hotwords_str,
        duration_sec=chunk_dur,
        audio_rms=raw_rms,
    ):
        return True
    toks = tokenize_strict(txt)
    if not toks:
        return False
    matched_distinct = {t for t in toks if t in VOX_HOTWORD_TOKENS}
    hw_ratio = sum(1 for t in toks if t in VOX_HOTWORD_TOKENS) / float(len(toks))
    if len(matched_distinct) >= 4 and (hw_ratio >= 0.55 or txt.count(",") >= 3):
        return True
    return False


def make_logits_processors(
    variant: str,
    processor: Any,
    blocked_ids_gpu: Any,
    prompt_len: int,
):
    import torch
    from transformers import LogitsProcessor, LogitsProcessorList

    tok = processor.tokenizer

    class ForeignScriptMaskLogitsProcessor(LogitsProcessor):
        def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
            if blocked_ids_gpu.numel() > 0:
                scores = scores.clone()
                scores[:, blocked_ids_gpu] = -float("inf")
            return scores

    class EnglishHindiLanguageGuardLogitsProcessor(LogitsProcessor):
        def __init__(self, in_len: int):
            self.in_len = in_len
            self.lang_word_id = tok.encode("language", add_special_tokens=False)[0]
            self.allowed_lang_ids = torch.tensor(
                [
                    tok.encode(" None", add_special_tokens=False)[0],
                    tok.encode(" English", add_special_tokens=False)[0],
                    tok.encode(" Hindi", add_special_tokens=False)[0],
                ],
                device="cuda",
                dtype=torch.long,
            )
            self.asr_text_id = tok.encode("<asr_text>", add_special_tokens=False)[0]

        def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
            step = int(input_ids.shape[1]) - self.in_len
            if step == 0:
                masked = torch.full_like(scores, -float("inf"))
                masked[:, self.lang_word_id] = scores[:, self.lang_word_id]
                return masked
            if step == 1:
                masked = torch.full_like(scores, -float("inf"))
                masked[:, self.allowed_lang_ids] = scores[:, self.allowed_lang_ids]
                return masked
            if step == 2:
                masked = torch.full_like(scores, -float("inf"))
                masked[:, self.asr_text_id] = scores[:, self.asr_text_id]
                return masked
            if blocked_ids_gpu.numel() > 0:
                scores = scores.clone()
                scores[:, blocked_ids_gpu] = -float("inf")
            return scores

    if variant == "native-guarded":
        return LogitsProcessorList([EnglishHindiLanguageGuardLogitsProcessor(prompt_len)])
    return LogitsProcessorList([ForeignScriptMaskLogitsProcessor()])


def _decode_chunk_qwen(
    model: Any,
    processor: Any,
    norm_sl: np.ndarray,
    *,
    variant: str,
    blocked_ids_gpu: Any,
    prompt: Optional[str],
    max_new_tokens: int = 320,
    repetition_penalty: float = 1.05,
    no_repeat_ngram_size: int = 5,
) -> Tuple[str, Optional[str], str, float]:
    import torch

    tc0 = time.perf_counter()
    inputs = processor.apply_transcription_request(
        audio=norm_sl,
        language=None,
        prompt=prompt,
        tokenize=True,
    )
    inputs = inputs.to("cuda")
    if "input_features" in inputs:
        inputs["input_features"] = inputs["input_features"].to(dtype=torch.float16)
    in_len = int(inputs["input_ids"].shape[1])
    lp_list = make_logits_processors(variant, processor, blocked_ids_gpu, in_len)
    with torch.inference_mode():
        gen_ids = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            repetition_penalty=repetition_penalty,
            no_repeat_ngram_size=no_repeat_ngram_size,
            logits_processor=lp_list,
        )
    raw_dec = processor.decode(gen_ids[0][in_len:], skip_special_tokens=True)
    del inputs, gen_ids
    infer_s = time.perf_counter() - tc0
    parsed = processor.parse_output(raw_dec)
    raw_txt = str(parsed.get("transcription") if isinstance(parsed, dict) else raw_dec).strip()
    det_lang = parsed.get("language") if isinstance(parsed, dict) else None
    return raw_txt, det_lang, raw_dec, infer_s


def run_ab_gpu_variant(variant: str, min_free_vram_mb: int) -> int:
    import torch

    used_mb, free_mb = _gpu_used_free()
    if free_mb is not None and free_mb < min_free_vram_mb:
        print(
            f"[AB2.5] VRAM GUARD STOP: free={free_mb} MB < {min_free_vram_mb} MB",
            file=sys.stderr,
            flush=True,
        )
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meetings, vocab = read_only_db_state()
    meeting_dirs = [Path(meetings[mid]["audio_path"]).parent for mid in (234, 235)]
    pre_snap = snapshot_protected_files(meeting_dirs)
    pre_db_sha = compute_target_db_rows_sha256()
    ref_files = {
        234: BENCH_DIR / "M234_hemanth_reference_draft.json",
        235: BENCH_DIR / "M235_rumjhum_reference_draft.json",
    }
    pre_ref_shas = {str(p.resolve()): _sha256_file(p) for p in ref_files.values()}

    if variant == "native-guarded":
        model_key = "qwen3-native-guarded"
        repo_id = "Qwen/Qwen3-ASR-0.6B-hf"
        local_dir = CHALLENGER_MODELS_DIR / "qwen3-asr-0.6b"
        rep_pen = 1.05
        no_rep_n = 5
    elif variant == "srota-guarded":
        model_key = "qwen3-srota-guarded"
        repo_id = "moorlee/qwen3-asr-0.6b-hinglish"
        local_dir = CHALLENGER_MODELS_DIR / "qwen3-asr-0.6b-hinglish"
        rep_pen = 1.06
        no_rep_n = 4
    else:
        raise ValueError(variant)

    _release_vram()
    vram_before, _ = _gpu_used_free()
    t_load0 = time.perf_counter()
    model, processor = _load_qwen3_asr_and_processor(repo_id, local_dir)
    load_sec = time.perf_counter() - t_load0
    blocked_ids = build_foreign_script_token_ids(processor.tokenizer)
    blocked_ids_gpu = torch.tensor(blocked_ids, device="cuda", dtype=torch.long)
    vram_after_load, free_after_load = _gpu_used_free()
    print(
        f"[AB2.5] Loaded {model_key} ({repo_id}) in {load_sec:.2f}s | blocked_foreign_tokens={len(blocked_ids)} "
        f"| VRAM={vram_after_load}MB used / {free_after_load}MB free",
        flush=True,
    )

    vad_p = dict(threshold=0.35, min_silence_duration_ms=500, speech_pad_ms=250)
    cpu_cores = max(1, os.cpu_count() or 1)
    run_meta: Dict[str, Any] = {
        "model_key": model_key,
        "variant": variant,
        "repo_id": repo_id,
        "decode_config": {
            "do_sample": False,
            "max_new_tokens": 320,
            "repetition_penalty": rep_pen,
            "no_repeat_ngram_size": no_rep_n,
            "speech_gated_hotwords": True,
            "hotword_gate_min_dur_sec": 1.5,
            "hotword_gate_min_rms": 0.012,
            "blocked_foreign_token_count": len(blocked_ids),
            "language_guard_allowed_prefixes": ["language None", "language English", "language Hindi"]
            if variant == "native-guarded"
            else "foreign_script_mask_only",
            "short_utterance_truncation_guard": variant == "srota-guarded",
        },
        "load_sec": round(load_sec, 2),
        "vram_before_load_mb": vram_before,
        "vram_after_load_mb": vram_after_load,
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
            chunk_lat_ms: List[float] = []
            peak_vram = vram_after_load or 0
            peak_ram = get_process_rss_mb()
            hotword_chunks = 0
            ungated_chunks = 0
            prompt_echo_retries = 0
            truncation_retries = 0
            lang_prefix_counts: Counter = Counter()

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
                    chunk_dur = sl.size / float(TARGET_SAMPLE_RATE)
                    raw_rms = float(np.sqrt(np.mean(np.square(sl))))
                    use_prompt = VOX_HOTWORDS if (chunk_dur >= 1.5 and raw_rms >= 0.012) else None
                    if use_prompt is not None:
                        hotword_chunks += 1
                    else:
                        ungated_chunks += 1

                    s_sec = round(s_i / float(TARGET_SAMPLE_RATE), 2)
                    e_sec = round(e_i / float(TARGET_SAMPLE_RATE), 2)
                    norm_sl = normalize_speech_audio(sl)

                    raw_txt, det_lang, raw_dec, infer_s = _decode_chunk_qwen(
                        model,
                        processor,
                        norm_sl,
                        variant=variant,
                        blocked_ids_gpu=blocked_ids_gpu,
                        prompt=use_prompt,
                        max_new_tokens=320,
                        repetition_penalty=rep_pen,
                        no_repeat_ngram_size=no_rep_n,
                    )

                    # Safeguard 1: Prompt-echo detection -> retry once without prompt
                    if use_prompt is not None and is_qwen_prompt_echo(raw_txt, use_prompt, chunk_dur, raw_rms):
                        prompt_echo_retries += 1
                        rt_txt, rt_lang, rt_dec, rt_s = _decode_chunk_qwen(
                            model,
                            processor,
                            norm_sl,
                            variant=variant,
                            blocked_ids_gpu=blocked_ids_gpu,
                            prompt=None,
                            max_new_tokens=320,
                            repetition_penalty=rep_pen,
                            no_repeat_ngram_size=no_rep_n,
                        )
                        infer_s += rt_s
                        if not is_qwen_prompt_echo(rt_txt, use_prompt, chunk_dur, raw_rms):
                            raw_txt, det_lang, raw_dec = rt_txt, rt_lang, rt_dec
                        else:
                            raw_txt = ""

                    # Safeguard 2 (Srota): Short-utterance truncation guard on genuine speech chunks
                    if variant == "srota-guarded" and chunk_dur >= 3.0 and raw_rms >= 0.015:
                        orig_toks = tokenize_strict(raw_txt)
                        if len(orig_toks) <= 2:
                            rt_txt, rt_lang, rt_dec, rt_s = _decode_chunk_qwen(
                                model,
                                processor,
                                norm_sl,
                                variant=variant,
                                blocked_ids_gpu=blocked_ids_gpu,
                                prompt=None,
                                max_new_tokens=320,
                                repetition_penalty=1.0,
                                no_repeat_ngram_size=no_rep_n,
                            )
                            infer_s += rt_s
                            rt_toks = tokenize_strict(rt_txt)
                            if (
                                len(rt_toks) > len(orig_toks)
                                and _count_duplication_words(rt_toks) == 0
                                and not is_qwen_prompt_echo(rt_txt, VOX_HOTWORDS, chunk_dur, raw_rms)
                            ):
                                truncation_retries += 1
                                raw_txt, det_lang, raw_dec = rt_txt, rt_lang, rt_dec

                    lang_prefix_counts[str(det_lang)] += 1
                    chunk_lat_ms.append(infer_s * 1000.0)
                    if chunk_idx % 12 == 0:
                        torch.cuda.empty_cache()

                    u_mb, _ = _gpu_used_free()
                    if u_mb is not None:
                        peak_vram = max(peak_vram, u_mb)
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
                            "used_hotword_prompt": bool(use_prompt),
                            "raw_text": raw_txt,
                            "text": norm_txt or raw_txt,
                        }
                    )

            all_segs.sort(key=lambda s: (float(s["start_time"]), float(s["end_time"])))
            for idx, s in enumerate(all_segs):
                s["segment_index"] = idx

            wall = time.perf_counter() - t0
            cpu_s = time.process_time() - c0
            pred_path = OUT_DIR / f"{model_key}_offline_M{mid}.json"
            pred_path.write_text(
                json.dumps({"meeting_id": mid, "title": m["title"], "model": model_key, "segments": all_segs}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            lat_arr = np.array(chunk_lat_ms, dtype=np.float64) if chunk_lat_ms else np.zeros(1)
            run_meta["meetings"][str(mid)] = {
                "wall_time_sec": round(wall, 2),
                "rtf": round(wall / max(0.01, dur), 4),
                "cpu_time_sec": round(cpu_s, 2),
                "avg_cpu_pct": round((cpu_s / max(0.01, wall)) * 100.0 / cpu_cores, 1),
                "peak_vram_used_mb": peak_vram,
                "peak_ram_rss_mb": round(peak_ram, 1),
                "chunk_count": len(chunk_lat_ms),
                "hotword_chunks": hotword_chunks,
                "ungated_chunks": ungated_chunks,
                "prompt_echo_retries": prompt_echo_retries,
                "truncation_retries": truncation_retries,
                "detected_language_counts": dict(lang_prefix_counts),
                "chunk_p50_ms": round(float(np.median(lat_arr)), 1),
                "chunk_p95_ms": round(float(np.percentile(lat_arr, 95)), 1),
                "segment_count": len(all_segs),
                "prediction_path": str(pred_path),
            }
            print(
                f"[AB2.5] {model_key} M#{mid}: wall={wall:.2f}s rtf={wall/dur:.4f} segs={len(all_segs)} "
                f"echo_retries={prompt_echo_retries} trunc_retries={truncation_retries} peak_vram={peak_vram}MB",
                flush=True,
            )
    finally:
        del model, blocked_ids_gpu
        _release_vram()
        vram_after_unload, vram_free_after = _gpu_used_free()
        run_meta["vram_after_unload_mb"] = vram_after_unload
        run_meta["vram_free_after_unload_mb"] = vram_free_after

    meta_path = OUT_DIR / f"{model_key}_meta.json"
    meta_path.write_text(json.dumps(run_meta, indent=2, ensure_ascii=False), encoding="utf-8")

    post_snap = snapshot_protected_files(meeting_dirs)
    post_db_sha = compute_target_db_rows_sha256()
    mismatches = [k for k in pre_snap if k not in post_snap or pre_snap[k] != post_snap[k]]
    if pre_db_sha != post_db_sha:
        mismatches.append("DB_TARGET_ROWS")
    for k, v in pre_ref_shas.items():
        if _sha256_file(Path(k)) != v:
            mismatches.append(k)
    print(f"[AB2.5] Saved {meta_path} | protected_intact={len(mismatches) == 0}", flush=True)
    if mismatches:
        raise RuntimeError(f"READ-ONLY INTEGRITY VIOLATION: {mismatches}")
    return 0


def _script_and_echo_audit(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    raw_cjk = raw_arab = raw_kana = raw_deva = 0
    fin_cjk = fin_arab = fin_kana = fin_deva = 0
    echo_segments = 0
    qwen_echo_segments = 0
    total_words = 0
    dup_words = 0
    raw_langs: Counter = Counter()

    for s in segments:
        rt = str(s.get("raw_text", ""))
        ft = str(s.get("text", ""))
        if rt:
            if CJK_RE.search(rt):
                raw_cjk += 1
            if ARAB_RE.search(rt):
                raw_arab += 1
            if KANA_HANGUL_CYR_RE.search(rt):
                raw_kana += 1
            if DEVA_RE.search(rt):
                raw_deva += 1
        if CJK_RE.search(ft):
            fin_cjk += 1
        if ARAB_RE.search(ft):
            fin_arab += 1
        if KANA_HANGUL_CYR_RE.search(ft):
            fin_kana += 1
        if DEVA_RE.search(ft):
            fin_deva += 1
        if "detected_language" in s:
            raw_langs[str(s.get("detected_language"))] += 1
        toks = tokenize_strict(ft)
        total_words += len(toks)
        dup_words += _count_duplication_words(toks)
        if is_qwen_prompt_echo(ft, VOX_HOTWORDS, 2.0, 0.01):
            qwen_echo_segments += 1

    echo_segments = count_prompt_echoes(segments)
    return {
        "segment_count": len(segments),
        "total_words": total_words,
        "duplication_words": dup_words,
        "duplication_rate": round(dup_words / max(1, total_words), 4),
        "raw_script_counts": {
            "cjk_segs": raw_cjk,
            "arabic_segs": raw_arab,
            "kana_hangul_cyrillic_segs": raw_kana,
            "devanagari_segs": raw_deva,
        },
        "final_script_counts": {
            "cjk_segs": fin_cjk,
            "arabic_segs": fin_arab,
            "kana_hangul_cyrillic_segs": fin_kana,
            "devanagari_segs": fin_deva,
        },
        "legacy_prompt_echo_count": echo_segments,
        "hotword_dump_echo_count": qwen_echo_segments,
        "detected_languages": dict(raw_langs),
    }


def build_ab_report() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    phase2_rpt = json.loads((PHASE2_DIR / "phase2_challenger_report.json").read_text(encoding="utf-8"))
    indic_rpt = json.loads((PHASE2_DIR / "indic_conformer_onnx_summary.json").read_text(encoding="utf-8"))
    meetings_db, _ = read_only_db_state()
    ref_drafts = {
        234: json.loads((BENCH_DIR / "M234_hemanth_reference_draft.json").read_text(encoding="utf-8")),
        235: json.loads((BENCH_DIR / "M235_rumjhum_reference_draft.json").read_text(encoding="utf-8")),
    }

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    baseline_segs: Dict[int, List[Dict[str, Any]]] = {}
    for mid in (234, 235):
        rows = conn.execute(
            "SELECT id, speaker, text, start_time, end_time, source_track FROM transcript_segments WHERE meeting_id=? ORDER BY start_time, id",
            (mid,),
        ).fetchall()
        baseline_segs[mid] = [dict(r) for r in rows]
    conn.close()

    native_guarded_meta = json.loads((OUT_DIR / "qwen3-native-guarded_meta.json").read_text(encoding="utf-8"))
    srota_guarded_meta = json.loads((OUT_DIR / "qwen3-srota-guarded_meta.json").read_text(encoding="utf-8"))

    candidates = [
        (
            "qwen3-native-baseline",
            lambda mid: PHASE2_DIR / f"qwen3-asr-0.6b_offline_M{mid}.json",
            phase2_rpt["challengers"]["qwen3-asr-0.6b"],
        ),
        (
            "qwen3-native-guarded",
            lambda mid: OUT_DIR / f"qwen3-native-guarded_offline_M{mid}.json",
            native_guarded_meta,
        ),
        (
            "qwen3-srota-baseline",
            lambda mid: PHASE2_DIR / f"qwen3-asr-0.6b-hinglish_offline_M{mid}.json",
            phase2_rpt["challengers"]["qwen3-asr-0.6b-hinglish"],
        ),
        (
            "qwen3-srota-guarded",
            lambda mid: OUT_DIR / f"qwen3-srota-guarded_offline_M{mid}.json",
            srota_guarded_meta,
        ),
        (
            "whisper-large-v3-turbo:offline",
            lambda mid: PHASE2_DIR / f"whisper-large-v3-turbo_offline_M{mid}.json",
            phase2_rpt["challengers"]["whisper-large-v3-turbo"],
        ),
        (
            "whisper-large-v3-turbo:live",
            lambda mid: PHASE2_DIR / f"whisper-large-v3-turbo_live_M{mid}.json",
            phase2_rpt["challengers"]["whisper-large-v3-turbo"],
        ),
        (
            "indic-conformer-onnx:offline",
            lambda mid: PHASE2_DIR / f"indic_conformer_onnx_M{mid}.json",
            indic_rpt,
        ),
    ]

    report: Dict[str, Any] = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hotwords_used": VOX_HOTWORDS,
        "models": {},
        "disagreement_excerpts": {"234": [], "235": []},
    }

    loaded_segs: Dict[int, Dict[str, List[Dict[str, Any]]]] = {234: {}, 235: {}}
    for mid in (234, 235):
        loaded_segs[mid]["baseline_db"] = baseline_segs[mid]

    # Score baseline_db first
    base_entry: Dict[str, Any] = {"meetings": {}}
    for mid in (234, 235):
        segs = baseline_segs[mid]
        base_entry["meetings"][str(mid)] = {
            "script_and_echo_audit": _script_and_echo_audit(segs),
            "scores": score_predictions_against_reference(ref_drafts[mid], segs),
        }
    report["models"]["baseline_db"] = base_entry

    for name, path_fn, meta_src in candidates:
        m_entry: Dict[str, Any] = {
            "load_sec": meta_src.get("load_sec"),
            "vram_after_load_mb": meta_src.get("vram_after_load_mb"),
            "peak_vram_used_mb": meta_src.get("peak_vram_used_mb"),
            "vram_after_unload_mb": meta_src.get("vram_after_unload_mb"),
            "decode_config": meta_src.get("decode_config"),
            "meetings": {},
        }
        for mid in (234, 235):
            segs = load_prediction_segments(path_fn(mid))
            loaded_segs[mid][name] = segs
            mm_raw = (meta_src.get("meetings") or {}).get(str(mid), {})
            if "offline_full" in mm_raw and name.endswith(":offline") or (
                "offline_full" in mm_raw and name.startswith("qwen3-")
            ):
                perf = mm_raw.get("offline_full", mm_raw)
            elif "live_continuous" in mm_raw and name.endswith(":live"):
                perf = mm_raw.get("live_continuous", {})
            else:
                perf = mm_raw
            m_entry["meetings"][str(mid)] = {
                "runtime": {
                    "wall_time_sec": perf.get("wall_time_sec") or perf.get("compute_wall_time_sec"),
                    "rtf": perf.get("rtf"),
                    "peak_vram_used_mb": perf.get("peak_vram_used_mb") or ((perf.get("gpu") or {}).get("used_mb")),
                    "peak_ram_rss_mb": perf.get("peak_ram_rss_mb") or perf.get("ram_rss_mb"),
                    "hotword_chunks": perf.get("hotword_chunks"),
                    "ungated_chunks": perf.get("ungated_chunks"),
                    "prompt_echo_retries": perf.get("prompt_echo_retries"),
                    "truncation_retries": perf.get("truncation_retries"),
                },
                "script_and_echo_audit": _script_and_echo_audit(segs),
                "scores": score_predictions_against_reference(ref_drafts[mid], segs),
            }
        report["models"][name] = m_entry

    # Extract 16+ human-aligned disagreement excerpts between A2 (`qwen3-native-guarded`) and B2 (`qwen3-srota-guarded`)
    for mid in (234, 235):
        ref_segs = ref_drafts[mid]["segments"]
        a1 = loaded_segs[mid]["qwen3-native-baseline"]
        a2 = loaded_segs[mid]["qwen3-native-guarded"]
        b1 = loaded_segs[mid]["qwen3-srota-baseline"]
        b2 = loaded_segs[mid]["qwen3-srota-guarded"]
        turbo = loaded_segs[mid]["whisper-large-v3-turbo:offline"]

        def _gather(segs: List[Dict[str, Any]], trk: str, rs: float, re_: float) -> str:
            hits = []
            for p in segs:
                if str(p.get("source_track", "")).lower() != trk:
                    continue
                ps, pe = float(p["start_time"]), float(p["end_time"])
                ov = min(pe, re_) - max(ps, rs)
                if ov > 0.25:
                    hits.append(str(p.get("text", "")).strip())
            return " ".join(h for h in hits if h)

        candidates_ex: List[Tuple[float, Dict[str, Any]]] = []
        for r in ref_segs:
            rs, re_ = float(r["start_time"]), float(r["end_time"])
            trk = str(r["source_track"])
            ref_txt = str(r.get("reference_text", "")).strip()
            a2_txt = _gather(a2, trk, rs, re_)
            b2_txt = _gather(b2, trk, rs, re_)
            if a2_txt.lower() == b2_txt.lower():
                continue
            a1_txt = _gather(a1, trk, rs, re_)
            b1_txt = _gather(b1, trk, rs, re_)
            t_txt = _gather(turbo, trk, rs, re_)
            # Prioritize segments with foreign-script fix in A1 vs A2, hotword/entity differences, or length divergence
            score = abs(len(a2_txt) - len(b2_txt)) / 20.0
            if CJK_RE.search(a1_txt) or ARAB_RE.search(a1_txt):
                score += 5.0
            if r.get("segment_index") in (100, 1, 2, 4):
                score += 4.0
            if any(w in ref_txt.lower() for w in ("hubspot", "upc", "wfx", "plm", "erp", "postman", "settings")):
                score += 3.0
            candidates_ex.append(
                (
                    score,
                    {
                        "segment_index": r["segment_index"],
                        "window": f"{rs:.1f}-{re_:.1f}s",
                        "source_track": trk,
                        "speaker": r["speaker"],
                        "uncertain": bool(r.get("uncertain")),
                        "reference_text": ref_txt,
                        "native_baseline_A1": a1_txt,
                        "native_guarded_A2": a2_txt,
                        "srota_baseline_B1": b1_txt,
                        "srota_guarded_B2": b2_txt,
                        "whisper_turbo_offline": t_txt,
                    },
                )
            )
        candidates_ex.sort(key=lambda x: x[0], reverse=True)
        limit = 8 if mid == 234 else 12
        report["disagreement_excerpts"][str(mid)] = [x[1] for x in candidates_ex[:limit]]

    AB_REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[AB2.5] Saved {AB_REPORT_PATH}", flush=True)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True, choices=["native-guarded", "srota-guarded", "build-ab-report"])
    parser.add_argument("--min-free-vram-mb", type=int, default=2400)
    args = parser.parse_args(argv)
    if args.variant == "build-ab-report":
        return build_ab_report()
    return run_ab_gpu_variant(args.variant, args.min_free_vram_mb)


if __name__ == "__main__":
    raise SystemExit(main())

"""
VOX Phase 0 Validation & Before/After Benchmark Suite.

Measures:
1. Protected DB SHA-256 lock verification (M#234, M#235, custom_vocabulary).
2. Read-only Microphone & System Audio + Silero VAD Calibration Audit (M#234, M#235, M#241).
3. First-Class English Accent Preservation (6 US-accented + 6 Indian-accented neural clips,
   plus real M#235 verified English segments) — measuring WER, CER, false Hindi flips, entity recall.
4. Hinglish Ground-Truth Benchmark (M#234 90s dual-track via official score_ground_truth scorer)
   and M#241 Daily Scrum garbled passages (U1..U5 + Mic 06:25-06:43).
5. Voice-Typing Hotword Leakage & Silence Hallucination Audit.
6. Resource Utilization (Windows working set / peak RSS MB, wall time, RTF, nvidia-smi VRAM telemetry).
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
from ctypes import wintypes
import inspect
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import av
import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"
for p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from benchmark_speech_intelligence import (
    BENCH_DIR,
    DB_PATH,
    compute_target_db_rows_sha256,
    read_only_db_state,
)
from score_ground_truth import (
    levenshtein_distance,
    normalize_strict_text,
    score_predictions_against_reference,
    tokenize_strict,
)
from voxlocal.audio.devices import TARGET_SAMPLE_RATE, normalize_speech_audio
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import (
    LocalWhisperTranscriber,
    is_hallucination,
    load_wav_as_16k_f32,
    load_wav_slice_as_16k_f32,
    merge_domain_hotwords,
)

EXPECTED_DB_HASH = "e632521df929d06ce9da552a2015cab96fe6e0a3c1e00200b1673c12ebb57439"
ACCENTS_DIR = BENCH_DIR / "phase0_english_accents"
OUT_DIR = BENCH_DIR / "phase0_validation"

# Parallel business & technical English test sentences synthesized in BOTH en-US and en-IN neural voices
ENGLISH_SENTENCE_SPECS: List[Dict[str, Any]] = [
    {
        "id": "01_plm_bom_ticket",
        "text": "I logged a blocker ticket in Jira for the WFX costing sheet export and BOM sync issue.",
        "entities": ["Jira", "WFX", "costing sheet", "BOM"],
    },
    {
        "id": "02_sprint_supplier_login",
        "text": "The supplier login portal is deployed on staging, so QA will verify it before Friday sprint review.",
        "entities": ["supplier", "staging", "QA", "Friday"],
    },
    {
        "id": "03_erp_hubspot_api",
        "text": "Please check the HubSpot API endpoint with Abhilash and confirm whether ERP orders match SKU counts.",
        "entities": ["HubSpot", "API", "Abhilash", "ERP", "SKU"],
    },
    {
        "id": "04_american_tall_windchill",
        "text": "American Tall and Troy Lee need updated Windchill PLM tech packs uploaded to the shared folder today.",
        "entities": ["American Tall", "Troy Lee", "Windchill", "PLM"],
    },
    {
        "id": "05_pr_merge_sqlite",
        "text": "Hemanth and Rumjhum approved the pull request merge for SQLite background job recovery in VOX.",
        "entities": ["Hemanth", "Rumjhum", "SQLite", "VOX"],
    },
    {
        "id": "06_weekly_standup_followup",
        "text": "Let us touch base at 11 AM on Microsoft Teams after checking Pankaj and Amar availability.",
        "entities": ["11", "Microsoft Teams", "Pankaj", "Amar"],
    },
]

VOICE_PAIRS = {
    "en_us": ("en-US-GuyNeural", "en-US-JennyNeural"),
    "en_in": ("en-IN-PrabhatNeural", "en-IN-NeerjaNeural"),
}


def get_peak_rss_mb() -> Tuple[float, float]:
    """Return (working_set_mb, peak_working_set_mb) via voxlocal.core.logger.get_system_resource_snapshot()."""
    from voxlocal.core.logger import get_system_resource_snapshot
    snap = get_system_resource_snapshot()
    return float(snap.get("working_set_mb") or 0.0), float(snap.get("peak_working_set_mb") or 0.0)


def query_nvidia_vram() -> Dict[str, Any]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3.0,
        ).strip()
        if out:
            parts = [p.strip() for p in out.splitlines()[0].split(",")]
            return {
                "gpu": parts[0],
                "total_mb": int(parts[1]),
                "used_mb": int(parts[2]),
                "free_mb": int(parts[3]),
            }
    except Exception as exc:
        return {"error": str(exc)}
    return {}


async def _synth_edge_tts_wav(text: str, voice: str, out_wav: Path) -> None:
    import edge_tts

    comm = edge_tts.Communicate(text, voice)
    mp3_buf = io.BytesIO()
    async for ch in comm.stream():
        if ch["type"] == "audio":
            mp3_buf.write(ch["data"])
    mp3_buf.seek(0)

    container = av.open(mp3_buf, format="mp3")
    resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_SAMPLE_RATE)
    pcm_chunks: List[bytes] = []
    for frame in container.decode(audio=0):
        out_frames = resampler.resample(frame)
        for of in out_frames:
            pcm_chunks.append(of.to_ndarray().tobytes())
    container.close()

    pcm_bytes = b"".join(pcm_chunks)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)


async def generate_english_accent_pack(force: bool = False) -> Path:
    ACCENTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_p = ACCENTS_DIR / "manifest.json"
    if manifest_p.exists() and not force:
        return manifest_p

    items: List[Dict[str, Any]] = []
    for group, (male_v, female_v) in VOICE_PAIRS.items():
        for idx, spec in enumerate(ENGLISH_SENTENCE_SPECS):
            voice = male_v if idx % 2 == 0 else female_v
            clip_id = f"{group}_{spec['id']}"
            wav_p = ACCENTS_DIR / f"{clip_id}.wav"
            if not wav_p.exists() or force:
                await _synth_edge_tts_wav(spec["text"], voice, wav_p)
            aud = load_wav_as_16k_f32(wav_p)
            dur = round(float(aud.size) / TARGET_SAMPLE_RATE, 3)
            rms = float(np.sqrt(np.mean(aud**2))) if aud.size else 0.0
            items.append(
                {
                    "id": clip_id,
                    "accent": group,
                    "voice": voice,
                    "wav_path": str(wav_p),
                    "duration_sec": dur,
                    "rms_dbfs": round(20.0 * np.log10(rms + 1e-9), 2),
                    "text": spec["text"],
                    "entities": spec["entities"],
                }
            )
    manifest_p.write_text(json.dumps({"clips": items}, indent=2), encoding="utf-8")
    return manifest_p


def audit_audio_and_vad() -> Dict[str, Any]:
    """Read-only signal & Silero VAD calibration audit across M#234, M#235, and M#241."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out: Dict[str, Any] = {}
    vad_opts = VadOptions(threshold=0.35, min_silence_duration_ms=700, speech_pad_ms=300)

    for mid in (234, 235, 241):
        row = dict(con.execute("SELECT id, title, duration_sec, mic_audio_path, sys_audio_path FROM meetings WHERE id=?", (mid,)).fetchone())
        m_entry: Dict[str, Any] = {"title": row["title"], "duration_sec": row["duration_sec"], "tracks": {}}
        for trk_key, col, gate in (("mic", "mic_audio_path", 0.0035), ("system", "sys_audio_path", 0.0012)):
            raw_p = LocalWhisperTranscriber._prefer_raw_wav_path(row.get(col) or "")
            if not raw_p or not Path(raw_p).exists():
                m_entry["tracks"][trk_key] = {"exists": False}
                continue
            aud = load_wav_as_16k_f32(raw_p)
            if aud.size == 0:
                m_entry["tracks"][trk_key] = {"exists": True, "samples": 0}
                continue
            win = int(0.25 * TARGET_SAMPLE_RATE)
            n_fr = aud.size // win
            fr = aud[: n_fr * win].reshape(n_fr, win)
            fr_rms = np.sqrt(np.mean(fr**2, axis=1))
            active_frames = fr_rms[fr_rms >= gate]
            p20 = float(np.percentile(fr_rms, 20))
            p50 = float(np.percentile(fr_rms, 50))
            p90 = float(np.percentile(fr_rms, 90))
            top20 = float(np.mean(np.sort(fr_rms)[int(0.8 * n_fr) :])) if n_fr >= 5 else float(np.mean(fr_rms))
            overall_rms = float(np.sqrt(np.mean(aud**2)))
            peak = float(np.max(np.abs(aud)))
            clip_cnt = int(np.sum(np.abs(aud) >= 0.999))

            norm_aud = normalize_speech_audio(aud)
            vad_raw = get_speech_timestamps(norm_aud, vad_opts)
            vad_speech_sec = sum((s["end"] - s["start"]) for s in vad_raw) / float(TARGET_SAMPLE_RATE)

            # Active speech above noise-adaptive threshold ignored by hard RMS gate
            noise_floor = max(p20, 1e-6)
            speech_like_total = int(np.sum(fr_rms >= max(noise_floor * 3.0, 0.0012)))
            speech_above_gate = int(np.sum(fr_rms >= gate))
            missed_by_gate_pct = round(
                100.0 * max(0, speech_like_total - speech_above_gate) / max(1, speech_like_total), 2
            )

            m_entry["tracks"][trk_key] = {
                "wav_path": raw_p,
                "duration_sec": round(aud.size / float(TARGET_SAMPLE_RATE), 2),
                "overall_rms_dbfs": round(20 * np.log10(overall_rms + 1e-9), 2),
                "peak_dbfs": round(20 * np.log10(peak + 1e-9), 2),
                "noise_floor_p20_dbfs": round(20 * np.log10(noise_floor + 1e-9), 2),
                "active_top20_rms_dbfs": round(20 * np.log10(top20 + 1e-9), 2),
                "p90_rms_dbfs": round(20 * np.log10(p90 + 1e-9), 2),
                "clipped_samples": clip_cnt,
                "rms_gate_threshold": gate,
                "frames_above_gate_pct": round(100.0 * active_frames.size / max(1, n_fr), 2),
                "missed_speech_like_frames_by_gate_pct": missed_by_gate_pct,
                "silero_vad_segments": len(vad_raw),
                "silero_vad_speech_sec": round(vad_speech_sec, 2),
                "silero_vad_pct_of_audio": round(100.0 * vad_speech_sec / max(0.1, aud.size / float(TARGET_SAMPLE_RATE)), 2),
            }
        out[f"M{mid}"] = m_entry
    con.close()
    return out


def _build_meeting_hotwords(vocab: List[Dict[str, Any]]) -> Tuple[str, str]:
    sig_bw = inspect.signature(DictationProcessor.build_whisper_hotwords)
    if "context" in sig_bw.parameters:
        base_hw = DictationProcessor.build_whisper_hotwords(vocab, language="en+hi", context="meeting")
    else:
        base_hw = DictationProcessor.build_whisper_hotwords(vocab, language="en+hi")

    sig_mh = inspect.signature(merge_domain_hotwords)
    if "context" in sig_mh.parameters:
        merged_hw = merge_domain_hotwords(base_hw, context="meeting")
    else:
        merged_hw = merge_domain_hotwords(base_hw)
    return base_hw, merged_hw


def _process_text_for_meeting(raw_txt: str, vocab: List[Dict[str, Any]], output_script: str = "roman") -> str:
    sig_p = inspect.signature(DictationProcessor.process)
    kw: Dict[str, Any] = dict(
        vocabulary=vocab,
        cleanup_enabled=True,
        spoken_punctuation=False,
        output_script=output_script,
    )
    if "context" in sig_p.parameters:
        kw["context"] = "meeting"
    return DictationProcessor.process(raw_txt, **kw).strip()


def _transcribe_clip(
    tr: LocalWhisperTranscriber,
    audio: np.ndarray,
    hotwords: str,
    vocab: List[Dict[str, Any]],
) -> Dict[str, Any]:
    norm_aud = normalize_speech_audio(audio)
    t0 = time.perf_counter()
    res = tr._transcribe_single_encode(
        norm_audio=norm_aud,
        language="en+hi",
        lang_arg=None,
        is_en_only=False,
        bilingual_modes=("", "auto", "en+hi", "hinglish", "hi_latn"),
        beam_size=2,
        vad_filter=False,
        vad_params={},
        hotwords=hotwords,
        temperatures=0.0,
        effective_prompt=None,
        word_timestamps=False,
    )
    dt = time.perf_counter() - t0
    segs, info = res if res else ([], None)
    raw = " ".join(str(getattr(s, "text", "")).strip() for s in (segs or [])).strip()
    lps = [float(getattr(s, "avg_logprob")) for s in (segs or []) if getattr(s, "avg_logprob", None) is not None]
    mean_lp = (sum(lps) / len(lps)) if lps else None
    det_lang = str(getattr(info, "language", "") or "")
    words = raw.split()
    uniq_ratio = (len(set(w.lower() for w in words)) / len(words)) if words else 1.0
    collapsed = bool("\ufffd" in raw or (len(words) >= 8 and uniq_ratio < 0.42))
    norm_txt = _process_text_for_meeting(raw, vocab, output_script="roman")
    return {
        "detected_language": det_lang,
        "raw_text": raw,
        "normalized_text": norm_txt,
        "mean_logprob": round(mean_lp, 4) if mean_lp is not None else None,
        "collapsed": collapsed,
        "infer_sec": round(dt, 3),
        "audio_sec": round(audio.size / float(TARGET_SAMPLE_RATE), 2),
    }


def _score_english_group(preds: List[Dict[str, Any]], refs: List[Dict[str, Any]]) -> Dict[str, Any]:
    all_ref_toks: List[str] = []
    all_hyp_toks: List[str] = []
    all_ref_chars: List[str] = []
    all_hyp_chars: List[str] = []
    hi_flips = 0
    ent_total = 0
    ent_hit = 0
    total_infer = 0.0
    total_aud = 0.0

    for p, r in zip(preds, refs):
        ref_t = tokenize_strict(r["text"])
        hyp_t = tokenize_strict(p["normalized_text"])
        all_ref_toks.extend(ref_t)
        all_hyp_toks.extend(hyp_t)
        ref_s = normalize_strict_text(r["text"])
        hyp_s = normalize_strict_text(p["normalized_text"])
        all_ref_chars.extend(list(ref_s))
        all_hyp_chars.extend(list(hyp_s))
        if p["detected_language"] != "en":
            hi_flips += 1
        hyp_low = p["normalized_text"].lower()
        for ent in r.get("entities", []):
            ent_total += 1
            if ent.lower() in hyp_low:
                ent_hit += 1
        total_infer += p["infer_sec"]
        total_aud += p["audio_sec"]

    wer = levenshtein_distance(all_ref_toks, all_hyp_toks) / max(1, len(all_ref_toks))
    cer = levenshtein_distance(all_ref_chars, all_hyp_chars) / max(1, len(all_ref_chars))
    return {
        "clip_count": len(refs),
        "wer": round(wer, 4),
        "cer": round(cer, 4),
        "false_hindi_flips": hi_flips,
        "entity_recall": round(ent_hit / max(1, ent_total), 4),
        "entity_hits": f"{ent_hit}/{ent_total}",
        "total_audio_sec": round(total_aud, 2),
        "total_infer_sec": round(total_infer, 2),
        "rtf": round(total_infer / max(0.1, total_aud), 3),
    }


def run_validation(mode: str) -> Dict[str, Any]:
    pre_hash = compute_target_db_rows_sha256()
    if pre_hash != EXPECTED_DB_HASH:
        raise RuntimeError(f"Protected DB hash mismatch BEFORE run: {pre_hash} != {EXPECTED_DB_HASH}")

    manifest_p = asyncio.run(generate_english_accent_pack(force=False))
    accent_clips = json.loads(manifest_p.read_text(encoding="utf-8"))["clips"]
    meetings, vocab = read_only_db_state()
    base_hw, merged_hw = _build_meeting_hotwords(vocab)

    ref_234 = json.loads((BENCH_DIR / "M234_hemanth_reference_draft.json").read_text(encoding="utf-8"))
    ref_235 = json.loads((BENCH_DIR / "M235_rumjhum_reference_draft.json").read_text(encoding="utf-8"))

    # Select M#235 verified English-only spans from system + mic tracks
    m235_en_spans = [
        s
        for s in ref_235["segments"]
        if s.get("language_tags") == ["en"]
        and s.get("verified_by_human")
        and not s.get("uncertain")
        and len(str(s.get("reference_text", "")).split()) >= 3
    ]
    m235_mic_raw = LocalWhisperTranscriber._prefer_raw_wav_path(meetings[235]["mic_audio_path"])
    m235_sys_raw = LocalWhisperTranscriber._prefer_raw_wav_path(meetings[235]["sys_audio_path"])

    # M#241 clips (Hinglish + English + Mic + Near-silence hotword leakage probe)
    m241_sys = r"D:\Milind\Voice Assistant\data\recordings\meeting_20261120_232715\system_track_raw.wav"
    m241_mic = r"D:\Milind\Voice Assistant\data\recordings\meeting_20261120_232715\mic_track_raw.wav"
    m241_specs = [
        ("U1_hinglish_00:14-00:31", m241_sys, 14.14, 31.21, "hinglish"),
        ("U2_hinglish_00:31-00:50", m241_sys, 31.21, 50.16, "hinglish"),
        ("U3_hinglish_01:08-01:15", m241_sys, 68.16, 75.10, "hinglish"),
        ("U4_hinglish_04:13-04:40", m241_sys, 253.86, 280.00, "hinglish"),
        ("U5_hinglish_04:45-05:05", m241_sys, 285.88, 305.00, "hinglish"),
        ("EN1_sys_00:50-01:04", m241_sys, 50.16, 64.18, "en"),
        ("EN2_sys_01:45-01:59", m241_sys, 105.26, 119.34, "en"),
        ("EN3_sys_03:44-03:56", m241_sys, 224.0, 236.0, "en"),
        ("MIC_hinglish_06:25-06:43", m241_mic, 385.0, 403.0, "hinglish"),
    ]

    leaked_tokens = [
        tok for tok in ("in caps", "all caps", "how I speak", "voice typing", "tell you step by step")
        if tok.lower() in merged_hw.lower()
    ]

    report: Dict[str, Any] = {
        "mode": mode,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "db_sha256_before": pre_hash,
        "nvidia_vram_idle": query_nvidia_vram(),
        "hotwords_audit": {
            "base_hotwords": base_hw,
            "merged_hotwords": merged_hw,
            "voice_typing_terms_leaked": leaked_tokens,
            "leaked_count": len(leaked_tokens),
        },
        "audio_and_vad_calibration": audit_audio_and_vad(),
        "models": {},
    }

    for model_name in ("small", "large-v3-turbo"):
        rss_before_mb, peak_before_mb = get_peak_rss_mb()
        t_load0 = time.perf_counter()
        tr = LocalWhisperTranscriber(
            models_dir=ROOT_DIR / "data" / "models",
            model_name=model_name,
            compute_device="cpu",
            compute_type="int8",
            cpu_threads=8 if model_name == "large-v3-turbo" else 6,
        )
        ok, msg = tr.ensure_loaded(model_name)
        load_sec = round(time.perf_counter() - t_load0, 3)
        if not ok:
            raise RuntimeError(f"Failed loading {model_name}: {msg}")
        rss_loaded_mb, _ = get_peak_rss_mb()

        # 1. US-accented & Indian-accented Neural English Packs
        us_clips = [c for c in accent_clips if c["accent"] == "en_us"]
        in_clips = [c for c in accent_clips if c["accent"] == "en_in"]
        us_preds = [_transcribe_clip(tr, load_wav_as_16k_f32(c["wav_path"]), merged_hw, vocab) for c in us_clips]
        in_preds = [_transcribe_clip(tr, load_wav_as_16k_f32(c["wav_path"]), merged_hw, vocab) for c in in_clips]
        us_summary = _score_english_group(us_preds, us_clips)
        in_summary = _score_english_group(in_preds, in_clips)

        # 2. Real Indian-accented English from M#235 verified ground truth
        m235_en_preds = []
        for s in m235_en_spans:
            w_path = m235_mic_raw if s.get("source_track") == "mic" else m235_sys_raw
            aud = load_wav_slice_as_16k_f32(w_path, float(s["start_time"]), float(s["end_time"]))
            p = _transcribe_clip(tr, aud, merged_hw, vocab)
            p["id"] = s.get("segment_index")
            p["ref"] = s.get("reference_text")
            m235_en_preds.append(p)
        m235_en_refs = [{"text": s["reference_text"], "entities": []} for s in m235_en_spans]
        m235_en_summary = _score_english_group(m235_en_preds, m235_en_refs)

        # 3. M#234 Full 90s Verified Dual-Track Benchmark via official scorer
        m234_mic = meetings[234]["mic_audio_path"]
        m234_sys = meetings[234]["sys_audio_path"]
        m234_mix = meetings[234]["audio_path"]
        t_234_0 = time.perf_counter()
        print(f"[{mode}] Scoring M#234 90s dual-track on {model_name}...", flush=True)
        m234_segs = tr.transcribe_meeting_dual_track(
            mixed_wav_path=m234_mix,
            mic_wav_path=m234_mic,
            sys_wav_path=m234_sys,
            initial_prompt="",
            language="en+hi",
            output_script="roman",
            remote_speaker_label="Hemanth Kumar Talathoti",
            hotwords=merged_hw,
            vocabulary=vocab,
        )
        m234_dt = time.perf_counter() - t_234_0
        m234_score = score_predictions_against_reference(ref_234, m234_segs)
        # Verify raw_text vs Romanization preservation on M#234
        deva_overwrite_count = sum(
            1
            for s in m234_segs
            if any("\u0900" <= ch <= "\u097F" for ch in (s.get("text") or ""))
        )
        raw_preserved_distinct = sum(
            1
            for s in m234_segs
            if s.get("raw_text") and any("\u0900" <= ch <= "\u097F" for ch in s.get("raw_text", ""))
        )

        # 4. M#241 Daily Scrum Clips
        m241_results: Dict[str, Any] = {}
        collapse_count = 0
        echo_count = 0
        for cname, wpath, s_t, e_t, kind in m241_specs:
            aud = load_wav_slice_as_16k_f32(wpath, s_t, e_t)
            res = _transcribe_clip(tr, aud, merged_hw, vocab)
            if res["collapsed"]:
                collapse_count += 1
            if any(ek in res["normalized_text"].lower() for ek in ("how i speak", "in caps", "all caps", "tell you step by step", "read ai")):
                echo_count += 1
            m241_results[cname] = res

        # Synthetic room noise silence probe (1.5s @ -52dBFS) with meeting hotwords to measure echo susceptibility
        rng = np.random.default_rng(42)
        low_noise = (rng.normal(0.0, 0.0025, int(2.0 * TARGET_SAMPLE_RATE))).astype(np.float32)
        noise_probe = _transcribe_clip(tr, low_noise, merged_hw, vocab)
        noise_hallucinated = bool(noise_probe["raw_text"] and not is_hallucination(noise_probe["raw_text"], duration_sec=2.0, audio_rms=0.0025))

        tr.unload_model()
        rss_after_mb, peak_after_mb = get_peak_rss_mb()

        report["models"][model_name] = {
            "load_sec": load_sec,
            "ram_rss_before_mb": rss_before_mb,
            "ram_rss_loaded_mb": rss_loaded_mb,
            "ram_peak_working_set_mb": peak_after_mb,
            "us_english_neural": us_summary,
            "us_english_samples": [{"id": c["id"], "ref": c["text"], **p} for c, p in zip(us_clips, us_preds)],
            "in_english_neural": in_summary,
            "in_english_samples": [{"id": c["id"], "ref": c["text"], **p} for c, p in zip(in_clips, in_preds)],
            "m235_real_english": m235_en_summary,
            "m235_real_english_samples": m235_en_preds,
            "m234_hinglish_full_90s": {
                "wall_sec": round(m234_dt, 2),
                "rtf": round(m234_dt / 90.15, 3),
                "segment_count": len(m234_segs),
                "raw_devanagari_preserved_segs": raw_preserved_distinct,
                "stream_hinglish_norm_wer": m234_score.get("stream_hinglish_norm_wer"),
                "stream_raw_wer": m234_score.get("stream_raw_wer"),
                "english_wer": m234_score.get("english_wer"),
                "hindi_code_switch_wer": m234_score.get("hindi_code_switch_wer"),
                "technical_entity_recall": m234_score.get("stream_technical_entity_recall"),
                "technical_entity_details": m234_score.get("stream_technical_entity_details"),
            },
            "m241_clips": m241_results,
            "m241_collapse_count": collapse_count,
            "m241_hotword_echo_count": echo_count,
            "low_noise_probe": {**noise_probe, "passed_hallucination_filter": noise_hallucinated},
        }

    report["nvidia_vram_after"] = query_nvidia_vram()
    post_hash = compute_target_db_rows_sha256()
    report["db_sha256_after"] = post_hash
    if post_hash != EXPECTED_DB_HASH:
        raise RuntimeError(f"Protected DB hash CHANGED during run: {post_hash} != {EXPECTED_DB_HASH}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fname = "baseline_before.json" if mode == "before" else "phase0_after.json"
    out_p = OUT_DIR / fname
    out_p.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out_p} | DB hash verified: {post_hash == EXPECTED_DB_HASH}")
    return report


def compare_reports() -> None:
    b_p = OUT_DIR / "baseline_before.json"
    a_p = OUT_DIR / "phase0_after.json"
    if not b_p.exists() or not a_p.exists():
        print("Missing baseline_before.json or phase0_after.json")
        return
    b = json.loads(b_p.read_text(encoding="utf-8"))
    a = json.loads(a_p.read_text(encoding="utf-8"))
    print(f"Hotword voice-typing leaks: BEFORE={b['hotwords_audit']['leaked_count']} ({b['hotwords_audit']['voice_typing_terms_leaked']}) -> AFTER={a['hotwords_audit']['leaked_count']}")
    for m in ("small", "large-v3-turbo"):
        bm, am = b["models"][m], a["models"][m]
        print(f"\n=== Model: {m} ===")
        for sec, label in [
            ("us_english_neural", "US English Neural"),
            ("in_english_neural", "IN English Neural"),
            ("m235_real_english", "M235 Real IN-English"),
        ]:
            bs, as_ = bm[sec], am[sec]
            print(f"  {label:22s}: WER {bs['wer']:.4f} -> {as_['wer']:.4f} | CER {bs['cer']:.4f} -> {as_['cer']:.4f} | HiFlips {bs['false_hindi_flips']} -> {as_['false_hindi_flips']} | Ent {bs['entity_hits']} -> {as_['entity_hits']}")
        b234, a234 = bm["m234_hinglish_full_90s"], am["m234_hinglish_full_90s"]
        print(f"  M234 90s Full         : NormWER {b234['stream_hinglish_norm_wer']} -> {a234['stream_hinglish_norm_wer']} | RawDevaPreserved {b234['raw_devanagari_preserved_segs']} -> {a234['raw_devanagari_preserved_segs']} | EntRec {b234['technical_entity_recall']} -> {a234['technical_entity_recall']}")
        print(f"  M241 Clips            : Collapses {bm['m241_collapse_count']} -> {am['m241_collapse_count']} | HotwordEchoes {bm['m241_hotword_echo_count']} -> {am['m241_hotword_echo_count']} | PeakRAM {bm['ram_peak_working_set_mb']} -> {am['ram_peak_working_set_mb']} MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("before", "after", "compare"), default="before")
    args = ap.parse_args()
    if args.mode == "compare":
        compare_reports()
    else:
        run_validation(args.mode)

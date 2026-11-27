"""
Phase 1 ASR Benchmark Corpus Preparation (Strictly Read-Only on Production Data).

Builds a multi-accent, multi-language, real-world benchmark corpus under
`data/benchmarks/phase1_corpus/` with per-track WAVs (16 kHz mono PCM16) and
`manifest.json`. Never touches `src/voxlocal/` or `data/voxlocal.db`.

Tracks created:
- Track A (US / International English):
    - `earnings22_us`: 40 real earnings-call segments from `distil-whisper/earnings22` (chunked/test)
    - `earnings22_us_continuous`: concatenated ~300s+ continuous stream from `earnings22_us`
    - `fleurs_en_us`: 40 distinct speakers/sentences from `google/fleurs` (en_us/test)
    - `phase0_us_neural`: 6 Phase 0 synthetic US clips (regression check only)
- Track B (Indian-Accented English):
    - `nptel_in_en`: 40 diverse STEM lectures from `skbose/indian-english-nptel-v0` (test)
    - `phase0_in_neural`: 6 Phase 0 synthetic Indian-English clips (regression check only)
- Track C (Pure Hindi):
    - `fleurs_hi_in`: 40 distinct sentences from `google/fleurs` (hi_in/test)
      with both Devanagari ground truth (`reference_devanagari`) and Romanized
      transliteration (`reference_roman`).
- Track D (Real Hinglish & Internal Meetings):
    - `m234_full_90s`: all 16 verified M#234 segments + dual-track full wav paths
    - `m235_verified_111`: all 111 verified M#235 segments + dual-track full wav paths
    - `m235_strat30`: deterministic 30-clip stratified subset of M#235
    - `m241_scrum_omission`: 9 diagnostic Daily Scrum clips (`U1..U5`, `EN1..EN3`, `MIC`)
    - `m240_us_in_call_5m`: 300s continuous real US+Indian business call (`system_track_raw.wav` 1140..1440s)
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import av
import numpy as np
from huggingface_hub import hf_hub_download

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"
for _p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from benchmark_speech_intelligence import DB_PATH, compute_target_db_rows_sha256
from voxlocal.audio.devices import TARGET_SAMPLE_RATE, resample_audio_to_16k_mono
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import LocalWhisperTranscriber, load_wav_as_16k_f32

EXPECTED_DB_HASH = "e632521df929d06ce9da552a2015cab96fe6e0a3c1e00200b1673c12ebb57439"
CORPUS_DIR = ROOT_DIR / "data" / "benchmarks" / "phase1_corpus"
WAVS_DIR = CORPUS_DIR / "wavs"
MANIFEST_PATH = CORPUS_DIR / "manifest.json"
PHASE0_ACCENT_MANIFEST = ROOT_DIR / "data" / "benchmarks" / "phase0_english_accents" / "manifest.json"
M234_REF_PATH = ROOT_DIR / "data" / "benchmarks" / "M234_hemanth_reference_draft.json"
M235_REF_PATH = ROOT_DIR / "data" / "benchmarks" / "M235_rumjhum_reference_draft.json"


def _http_get_bytes(url: str, *, timeout: int = 30, retries: int = 4) -> bytes:
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "voxlocal-phase1-bench/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed HTTP GET {url}: {last_err}")


def _http_get_json(url: str) -> Dict[str, Any]:
    raw = _http_get_bytes(url, timeout=30, retries=4)
    return json.loads(raw.decode("utf-8"))


def decode_bytes_to_16k_mono_pcm16(raw_bytes: bytes) -> np.ndarray:
    buf = io.BytesIO(raw_bytes)
    with av.open(buf) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_SAMPLE_RATE)
        chunks: List[np.ndarray] = []
        for frame in container.decode(stream):
            out_frames = resampler.resample(frame)
            if not isinstance(out_frames, list):
                out_frames = [out_frames]
            for out_f in out_frames:
                if out_f is not None:
                    chunks.append(out_f.to_ndarray().reshape(-1))
        flushed = resampler.resample(None)
        if flushed:
            if not isinstance(flushed, list):
                flushed = [flushed]
            for out_f in flushed:
                if out_f is not None:
                    chunks.append(out_f.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros((0,), dtype=np.int16)
    return np.concatenate(chunks).astype(np.int16, copy=False)


def write_pcm16_wav(path: Path, samples_i16: np.ndarray, sample_rate: int = TARGET_SAMPLE_RATE) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.asarray(samples_i16, dtype=np.int16).reshape(-1)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return validate_wav(path)


def write_f32_wav(path: Path, samples_f32: np.ndarray, sample_rate: int = TARGET_SAMPLE_RATE) -> Dict[str, Any]:
    clipped = np.clip(np.asarray(samples_f32, dtype=np.float32), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(np.int16)
    return write_pcm16_wav(path, pcm, sample_rate=sample_rate)


def validate_wav(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing WAV: {path}")
    with wave.open(str(path), "rb") as wf:
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        nf = wf.getnframes()
        raw = wf.readframes(nf)
    if ch != 1 or sw != 2 or sr != TARGET_SAMPLE_RATE or nf <= 0:
        raise ValueError(f"Invalid WAV format {path}: ch={ch} sw={sw} sr={sr} nf={nf}")
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(np.square(arr)))) if arr.size else 0.0
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    dur = float(nf) / float(sr)
    return {
        "duration_sec": round(dur, 3),
        "rms": round(rms, 6),
        "peak": round(peak, 6),
        "nframes": int(nf),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()[:16],
    }


def _extract_audio_src(audio_field: Any) -> Optional[str]:
    if isinstance(audio_field, list) and audio_field:
        first = audio_field[0]
        if isinstance(first, dict) and first.get("src"):
            return str(first["src"])
    elif isinstance(audio_field, dict) and audio_field.get("src"):
        return str(audio_field["src"])
    return None


def _load_or_fetch_rows(ds: str, config: str, split: str, offsets: List[int], length: int = 100) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for off in offsets:
        url = (
            f"https://datasets-server.huggingface.co/rows"
            f"?dataset={ds}&config={config}&split={split}&offset={off}&length={length}"
        )
        data = _http_get_json(url)
        for item in data.get("rows", []):
            r = dict(item.get("row") or {})
            r["_row_idx"] = item.get("row_idx")
            rows.append(r)
    return rows


def prepare_earnings22_us(target_count: int = 40) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    print("[Corpus] Preparing earnings22_us...")
    offsets = [0, 1200, 3500, 7000, 12000, 18000, 25000, 33000, 42000, 50000]
    rows = _load_or_fetch_rows("distil-whisper/earnings22", "chunked", "test", offsets, length=80)

    # Filter & spread evenly across file_ids
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        txt = str(r.get("transcription") or "").strip()
        if not txt or "<" in txt or len(txt.split()) < 6:
            continue
        src_url = _extract_audio_src(r.get("audio"))
        if not src_url:
            continue
        try:
            dur_est = float(r.get("end_ts", 0.0)) - float(r.get("start_ts", 0.0))
        except Exception:
            dur_est = 0.0
        if dur_est < 3.5 or dur_est > 18.0:
            continue
        fid = str(r.get("file_id") or "")
        by_file.setdefault(fid, []).append(r)

    # Round-robin pick across distinct file_ids for high speaker/company diversity
    fids = sorted(by_file.keys())
    selected_candidates: List[Dict[str, Any]] = []
    idx = 0
    while len(selected_candidates) < target_count + 12 and fids:
        progressed = False
        for fid in fids:
            bucket = by_file[fid]
            if idx < len(bucket):
                selected_candidates.append(bucket[idx])
                progressed = True
                if len(selected_candidates) >= target_count + 12:
                    break
        if not progressed:
            break
        idx += 1

    clips: List[Dict[str, Any]] = []
    out_dir = WAVS_DIR / "earnings22_us"
    for r in selected_candidates:
        if len(clips) >= target_count:
            break
        cid = f"e22_{r.get('file_id')}_{r.get('segment_id')}_{r.get('_row_idx')}"
        wav_path = out_dir / f"{cid}.wav"
        try:
            if wav_path.exists():
                meta = validate_wav(wav_path)
            else:
                src_url = _extract_audio_src(r.get("audio"))
                raw_bytes = _http_get_bytes(str(src_url), timeout=30, retries=3)
                pcm = decode_bytes_to_16k_mono_pcm16(raw_bytes)
                meta = write_pcm16_wav(wav_path, pcm)
            if meta["duration_sec"] < 3.0 or meta["rms"] < 0.002:
                wav_path.unlink(missing_ok=True)
                continue
            clips.append({
                "id": cid,
                "track": "earnings22_us",
                "language": "en",
                "accent": "en_us_real",
                "speaker_id": f"file_{r.get('file_id')}",
                "file_id": str(r.get("file_id")),
                "segment_id": str(r.get("segment_id")),
                "start_ts": float(r.get("start_ts", 0.0)),
                "end_ts": float(r.get("end_ts", 0.0)),
                "wav_path": str(wav_path),
                "reference_text": str(r.get("transcription") or "").strip(),
                "duration_sec": meta["duration_sec"],
                "rms": meta["rms"],
                "is_synthetic_regression": False,
            })
        except Exception as exc:
            print(f"  [warn] skipped {cid}: {exc}")

    # Build continuous concatenated stream sorted cleanly by (file_id, start_ts)
    clips_sorted = sorted(clips, key=lambda c: (c["file_id"], c["start_ts"], c["id"]))
    silence_pad = np.zeros(int(0.4 * TARGET_SAMPLE_RATE), dtype=np.float32)
    stitched_parts: List[np.ndarray] = []
    stitched_refs: List[str] = []
    timeline: List[Dict[str, Any]] = []
    cursor_sec = 0.0
    for c in clips_sorted:
        arr = load_wav_as_16k_f32(c["wav_path"])
        stitched_parts.append(arr)
        timeline.append({
            "clip_id": c["id"],
            "start_sec": round(cursor_sec, 3),
            "end_sec": round(cursor_sec + c["duration_sec"], 3),
            "reference_text": c["reference_text"],
        })
        cursor_sec += float(arr.size) / float(TARGET_SAMPLE_RATE)
        stitched_parts.append(silence_pad)
        cursor_sec += 0.4
        stitched_refs.append(c["reference_text"])

    cont_wav = CORPUS_DIR / "earnings22_us_continuous.wav"
    cont_arr = np.concatenate(stitched_parts) if stitched_parts else np.zeros((0,), dtype=np.float32)
    cont_meta = write_f32_wav(cont_wav, cont_arr)
    cont_spec = {
        "id": "earnings22_us_continuous",
        "wav_path": str(cont_wav),
        "duration_sec": cont_meta["duration_sec"],
        "rms": cont_meta["rms"],
        "clip_count": len(clips_sorted),
        "reference_text": " ".join(stitched_refs),
        "timeline": timeline,
    }
    print(f"  [OK] earnings22_us: {len(clips)} clips ({sum(c['duration_sec'] for c in clips):.1f}s), "
          f"file_ids={len(set(c['file_id'] for c in clips))}, cont={cont_meta['duration_sec']:.1f}s")
    return clips, cont_spec


def prepare_nptel_in_en(target_count: int = 40) -> List[Dict[str, Any]]:
    print("[Corpus] Preparing nptel_in_en...")
    offsets = [0, 6000, 14000, 24000, 36000, 48000, 62000, 76000, 90000, 102000]
    rows = _load_or_fetch_rows("skbose/indian-english-nptel-v0", "default", "test", offsets, length=80)

    by_spk: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        txt_norm = str(r.get("transcription_normalised") or "").strip()
        txt_raw = str(r.get("transcription") or "").strip()
        ref = txt_norm if txt_norm else txt_raw.lower()
        if not ref or len(ref.split()) < 6:
            continue
        if not _extract_audio_src(r.get("audio")):
            continue
        spk = str(r.get("speaker_name") or "unknown").strip()
        by_spk.setdefault(spk, []).append(r)

    speakers = sorted(by_spk.keys())
    selected_candidates: List[Dict[str, Any]] = []
    idx = 0
    while len(selected_candidates) < target_count + 20 and speakers:
        progressed = False
        for spk in speakers:
            bucket = by_spk[spk]
            if idx < len(bucket):
                selected_candidates.append(bucket[idx])
                progressed = True
                if len(selected_candidates) >= target_count + 20:
                    break
        if not progressed:
            break
        idx += 1

    clips: List[Dict[str, Any]] = []
    out_dir = WAVS_DIR / "nptel_in_en"
    for r in selected_candidates:
        if len(clips) >= target_count:
            break
        fn = str(r.get("file_name") or r.get("_row_idx"))[:24]
        cid = f"nptel_{fn}_{r.get('_row_idx')}"
        wav_path = out_dir / f"{cid}.wav"
        try:
            if wav_path.exists():
                meta = validate_wav(wav_path)
            else:
                src_url = _extract_audio_src(r.get("audio"))
                raw_bytes = _http_get_bytes(str(src_url), timeout=30, retries=3)
                pcm = decode_bytes_to_16k_mono_pcm16(raw_bytes)
                meta = write_pcm16_wav(wav_path, pcm)
            if meta["duration_sec"] < 2.5 or meta["duration_sec"] > 24.0 or meta["rms"] < 0.0015:
                wav_path.unlink(missing_ok=True)
                continue
            ref_norm = str(r.get("transcription_normalised") or "").strip()
            ref_raw = str(r.get("transcription") or "").strip()
            clips.append({
                "id": cid,
                "track": "nptel_in_en",
                "language": "en",
                "accent": "en_in_real",
                "speaker_id": str(r.get("speaker_name") or "unknown"),
                "wav_path": str(wav_path),
                "reference_text": ref_norm or ref_raw.lower(),
                "raw_transcription": ref_raw,
                "duration_sec": meta["duration_sec"],
                "rms": meta["rms"],
                "is_synthetic_regression": False,
            })
        except Exception as exc:
            print(f"  [warn] skipped {cid}: {exc}")

    print(f"  [OK] nptel_in_en: {len(clips)} clips ({sum(c['duration_sec'] for c in clips):.1f}s), "
          f"speakers={len(set(c['speaker_id'] for c in clips))}")
    return clips


def prepare_fleurs_track(lang_cfg: str, track_name: str, language: str, accent: str, target_count: int = 40) -> List[Dict[str, Any]]:
    print(f"[Corpus] Preparing {track_name} (google/fleurs {lang_cfg})...")
    tsv_path = Path(hf_hub_download(repo_id="google/fleurs", filename=f"data/{lang_cfg}/test.tsv", repo_type="dataset"))
    tar_path = Path(hf_hub_download(repo_id="google/fleurs", filename=f"data/{lang_cfg}/audio/test.tar.gz", repo_type="dataset"))

    # Parse TSV & pick distinct sentence IDs balanced across MALE/FEMALE with 4s..18s duration
    male_rows: List[Dict[str, Any]] = []
    female_rows: List[Dict[str, Any]] = []
    seen_ids = set()
    with tsv_path.open("r", encoding="utf-8") as f:
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 7:
                continue
            sent_id, fname, raw_txt, norm_txt, _chars, num_samples_s, gender = cols[:7]
            if sent_id in seen_ids:
                continue
            try:
                dur_est = float(num_samples_s) / float(TARGET_SAMPLE_RATE)
            except Exception:
                continue
            if dur_est < 4.5 or dur_est > 18.0 or not raw_txt.strip():
                continue
            seen_ids.add(sent_id)
            rec = {
                "sent_id": sent_id,
                "file_name": fname.strip(),
                "raw_txt": raw_txt.strip(),
                "norm_txt": norm_txt.strip(),
                "gender": gender.strip(),
                "dur_est": dur_est,
            }
            if gender.strip().upper() == "FEMALE":
                female_rows.append(rec)
            else:
                male_rows.append(rec)

    selected: List[Dict[str, Any]] = []
    half = target_count // 2
    # Deterministic even stride across lists
    def _pick_strided(pool: List[Dict[str, Any]], k: int) -> List[Dict[str, Any]]:
        if len(pool) <= k:
            return pool
        step = len(pool) / float(k)
        return [pool[int(i * step)] for i in range(k)]

    selected.extend(_pick_strided(female_rows, half))
    selected.extend(_pick_strided(male_rows, target_count - len(selected)))
    wanted_names = {r["file_name"]: r for r in selected}

    out_dir = WAVS_DIR / track_name
    out_dir.mkdir(parents=True, exist_ok=True)
    # Check if already extracted cleanly
    missing_names = {fn for fn in wanted_names if not (out_dir / fn).exists()}
    if missing_names:
        print(f"  Extracting {len(missing_names)} wavs from {tar_path.name}...")
        with tarfile.open(tar_path, "r:gz") as tf:
            for member in tf:
                if not member.isfile():
                    continue
                base = Path(member.name).name
                if base in missing_names:
                    fobj = tf.extractfile(member)
                    if fobj is None:
                        continue
                    raw_bytes = fobj.read()
                    pcm = decode_bytes_to_16k_mono_pcm16(raw_bytes)
                    write_pcm16_wav(out_dir / base, pcm)
                    missing_names.discard(base)
                    if not missing_names:
                        break

    clips: List[Dict[str, Any]] = []
    for r in selected:
        wav_path = out_dir / r["file_name"]
        if not wav_path.exists():
            continue
        meta = validate_wav(wav_path)
        item: Dict[str, Any] = {
            "id": f"{track_name}_{r['sent_id']}_{wav_path.stem[:10]}",
            "track": track_name,
            "language": language,
            "accent": accent,
            "gender": r["gender"],
            "speaker_id": f"{track_name}_{r['sent_id']}",
            "wav_path": str(wav_path),
            "duration_sec": meta["duration_sec"],
            "rms": meta["rms"],
            "is_synthetic_regression": False,
        }
        if language == "hi":
            item["reference_devanagari"] = r["raw_txt"]
            item["reference_roman"] = DictationProcessor.devanagari_to_roman_hinglish(r["raw_txt"]).strip()
            item["reference_text"] = r["raw_txt"]
        else:
            item["reference_text"] = r["raw_txt"]
        clips.append(item)

    print(f"  [OK] {track_name}: {len(clips)} clips ({sum(c['duration_sec'] for c in clips):.1f}s)")
    return clips


def prepare_phase0_neural() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    data = json.loads(PHASE0_ACCENT_MANIFEST.read_text(encoding="utf-8"))
    us_clips: List[Dict[str, Any]] = []
    in_clips: List[Dict[str, Any]] = []
    for c in data["clips"]:
        meta = validate_wav(Path(c["wav_path"]))
        rec = {
            "id": c["id"],
            "track": "phase0_us_neural" if c["accent"] == "en_us" else "phase0_in_neural",
            "language": "en",
            "accent": c["accent"],
            "speaker_id": c["voice"],
            "wav_path": c["wav_path"],
            "reference_text": c["text"],
            "entities": c.get("entities") or [],
            "duration_sec": meta["duration_sec"],
            "rms": meta["rms"],
            "is_synthetic_regression": True,
        }
        if c["accent"] == "en_us":
            us_clips.append(rec)
        else:
            in_clips.append(rec)
    print(f"  [OK] phase0 neural regression: US={len(us_clips)} clips, IN={len(in_clips)} clips")
    return us_clips, in_clips


def _slice_meeting_segments(
    meeting_id: int,
    ref_path: Path,
    track_name: str,
    rec_dir: Path,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    print(f"[Corpus] Slicing {track_name} (M#{meeting_id})...")
    ref_data = json.loads(ref_path.read_text(encoding="utf-8"))
    mic_path = Path(LocalWhisperTranscriber._prefer_raw_wav_path(str(rec_dir / "mic_track.wav")))
    sys_path = Path(LocalWhisperTranscriber._prefer_raw_wav_path(str(rec_dir / "system_track.wav")))
    mix_path = rec_dir / "meeting_mixed.wav"
    mic_arr = load_wav_as_16k_f32(str(mic_path))
    sys_arr = load_wav_as_16k_f32(str(sys_path))

    out_dir = WAVS_DIR / track_name
    out_dir.mkdir(parents=True, exist_ok=True)
    clips: List[Dict[str, Any]] = []
    for seg in ref_data["segments"]:
        if not seg.get("verified_by_human"):
            continue
        idx = int(seg["segment_index"])
        st = float(seg["start_time"])
        et = float(seg["end_time"])
        src_track = str(seg.get("source_track") or "mic")
        arr = mic_arr if src_track == "mic" else sys_arr
        s_i = max(0, int(round(st * TARGET_SAMPLE_RATE)))
        e_i = min(arr.size, int(round(et * TARGET_SAMPLE_RATE)))
        if e_i <= s_i:
            continue
        sl = arr[s_i:e_i]
        wav_path = out_dir / f"m{meeting_id}_seg_{idx:03d}_{src_track}.wav"
        if wav_path.exists():
            meta = validate_wav(wav_path)
        else:
            meta = write_f32_wav(wav_path, sl)
        clips.append({
            "id": f"m{meeting_id}_seg_{idx:03d}",
            "segment_index": idx,
            "meeting_id": meeting_id,
            "track": track_name,
            "source_track": src_track,
            "speaker": seg.get("speaker") or "",
            "speaker_cluster": seg.get("speaker_cluster") or "",
            "start_time": st,
            "end_time": et,
            "language_tags": seg.get("language_tags") or [],
            "uncertain": bool(seg.get("uncertain", False)),
            "reference_text": str(seg.get("reference_text") or ""),
            "wav_path": str(wav_path),
            "duration_sec": meta["duration_sec"],
            "rms": meta["rms"],
            "is_synthetic_regression": False,
        })
    full_tracks = {
        "mic": str(mic_path),
        "system": str(sys_path),
        "mixed": str(mix_path),
    }
    print(f"  [OK] {track_name}: {len(clips)} verified clips ({sum(c['duration_sec'] for c in clips):.1f}s)")
    return clips, full_tracks


def build_m235_strat30(m235_clips: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Deterministic stratified 30: up to 10 pure en / mixed-en, 10 pure hi / hi+hinglish, 10 hinglish across mic & system
    en_pool = [c for c in m235_clips if c["language_tags"] == ["en"] and c["duration_sec"] >= 1.2]
    hi_pool = [c for c in m235_clips if "hi" in c["language_tags"] and c["duration_sec"] >= 2.0]
    mix_pool = [c for c in m235_clips if c["language_tags"] == ["hinglish"] and c["duration_sec"] >= 2.5]
    filler_pool = [c for c in m235_clips if not c["language_tags"] or c["duration_sec"] < 2.0]

    def _even(pool: List[Dict[str, Any]], k: int, seen: set) -> List[Dict[str, Any]]:
        clean = [p for p in pool if p["id"] not in seen]
        if len(clean) <= k:
            out = list(clean)
        else:
            step = len(clean) / float(k)
            out = [clean[int(i * step)] for i in range(k)]
        for x in out:
            seen.add(x["id"])
        return out

    seen = set()
    picked: List[Dict[str, Any]] = []
    picked.extend(_even(en_pool, 8, seen))
    picked.extend(_even(hi_pool, 10, seen))
    picked.extend(_even(mix_pool, 10, seen))
    picked.extend(_even(filler_pool, 30 - len(picked), seen))
    if len(picked) < 30:
        picked.extend(_even(m235_clips, 30 - len(picked), seen))
    picked_sorted = sorted(picked, key=lambda c: c["segment_index"])
    print(f"  [OK] m235_strat30: {len(picked_sorted)} clips ({sum(c['duration_sec'] for c in picked_sorted):.1f}s)")
    return picked_sorted


def prepare_m241_scrum_omission() -> List[Dict[str, Any]]:
    print("[Corpus] Slicing m241_scrum_omission (M#241)...")
    rec_dir = ROOT_DIR / "data" / "recordings" / "meeting_20261120_232715"
    sys_arr = load_wav_as_16k_f32(str(rec_dir / "system_track_raw.wav"))
    mic_arr = load_wav_as_16k_f32(str(rec_dir / "mic_track_raw.wav"))
    specs = [
        ("U1_hinglish_00:14-00:31", "system", 14.14, 31.21, "hinglish"),
        ("U2_hinglish_00:31-00:50", "system", 31.21, 50.16, "hinglish"),
        ("U3_hinglish_01:08-01:15", "system", 68.16, 75.10, "hinglish"),
        ("U4_hinglish_04:13-04:40", "system", 253.86, 280.00, "hinglish"),
        ("U5_hinglish_04:45-05:05", "system", 285.88, 305.00, "hinglish"),
        ("EN1_sys_00:50-01:04", "system", 50.16, 64.18, "en"),
        ("EN2_sys_01:45-01:59", "system", 105.26, 119.34, "en"),
        ("EN3_sys_03:44-03:56", "system", 224.89, 236.89, "en"),
        ("MIC_hinglish_06:25-06:43", "mic", 385.00, 403.00, "hinglish"),
    ]
    out_dir = WAVS_DIR / "m241_scrum_omission"
    out_dir.mkdir(parents=True, exist_ok=True)
    clips: List[Dict[str, Any]] = []
    for cid, track, st, et, exp_lang in specs:
        arr = sys_arr if track == "system" else mic_arr
        s_i = max(0, int(round(st * TARGET_SAMPLE_RATE)))
        e_i = min(arr.size, int(round(et * TARGET_SAMPLE_RATE)))
        safe_name = cid.replace(":", "-") + ".wav"
        wav_path = out_dir / safe_name
        meta = validate_wav(wav_path) if wav_path.exists() else write_f32_wav(wav_path, arr[s_i:e_i])
        clips.append({
            "id": cid,
            "track": "m241_scrum_omission",
            "source_track": track,
            "expected_language_group": exp_lang,
            "start_time": st,
            "end_time": et,
            "wav_path": str(wav_path),
            "duration_sec": meta["duration_sec"],
            "rms": meta["rms"],
            "is_synthetic_regression": False,
        })
    print(f"  [OK] m241_scrum_omission: {len(clips)} clips ({sum(c['duration_sec'] for c in clips):.1f}s)")
    return clips


def prepare_m240_5m_slice() -> Dict[str, Any]:
    print("[Corpus] Slicing m240_us_in_call_5m (M#240 system_track_raw 1140..1440s)...")
    src_wav = ROOT_DIR / "data" / "recordings" / "meeting_20261119_223231" / "system_track_raw.wav"
    arr = load_wav_as_16k_f32(str(src_wav))
    s_i = int(round(1140.0 * TARGET_SAMPLE_RATE))
    e_i = min(arr.size, int(round(1440.0 * TARGET_SAMPLE_RATE)))
    out_wav = CORPUS_DIR / "m240_us_in_call_5m.wav"
    meta = validate_wav(out_wav) if out_wav.exists() else write_f32_wav(out_wav, arr[s_i:e_i])
    print(f"  [OK] m240_us_in_call_5m: {meta['duration_sec']:.2f}s rms={meta['rms']}")
    return {
        "id": "m240_us_in_call_5m",
        "source_wav": str(src_wav),
        "start_time": 1140.0,
        "end_time": 1440.0,
        "wav_path": str(out_wav),
        "duration_sec": meta["duration_sec"],
        "rms": meta["rms"],
        "note": "Unverified real US+Indian call continuous stream for throughput, hallucination, and language-stability stress testing.",
    }


def main() -> int:
    db_hash_before = compute_target_db_rows_sha256()
    if db_hash_before != EXPECTED_DB_HASH:
        raise RuntimeError(f"DB hash mismatch before corpus prep: {db_hash_before}")

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    WAVS_DIR.mkdir(parents=True, exist_ok=True)

    e22_clips, e22_cont = prepare_earnings22_us(target_count=40)
    fleurs_en_clips = prepare_fleurs_track("en_us", "fleurs_en_us", "en", "en_us_real", target_count=40)
    p0_us, p0_in = prepare_phase0_neural()
    nptel_clips = prepare_nptel_in_en(target_count=40)
    fleurs_hi_clips = prepare_fleurs_track("hi_in", "fleurs_hi_in", "hi", "hi_in_real", target_count=40)

    m234_clips, m234_full = _slice_meeting_segments(
        234, M234_REF_PATH, "m234_full_90s", ROOT_DIR / "data" / "recordings" / "meeting_20261119_200303"
    )
    m235_clips, m235_full = _slice_meeting_segments(
        235, M235_REF_PATH, "m235_verified_111", ROOT_DIR / "data" / "recordings" / "meeting_20261119_200840"
    )
    m235_strat30 = build_m235_strat30(m235_clips)
    m241_clips = prepare_m241_scrum_omission()
    m240_cont = prepare_m240_5m_slice()

    tracks = {
        "earnings22_us": e22_clips,
        "fleurs_en_us": fleurs_en_clips,
        "phase0_us_neural": p0_us,
        "nptel_in_en": nptel_clips,
        "phase0_in_neural": p0_in,
        "fleurs_hi_in": fleurs_hi_clips,
        "m234_full_90s": m234_clips,
        "m235_verified_111": m235_clips,
        "m235_strat30": m235_strat30,
        "m241_scrum_omission": m241_clips,
    }

    summary = {
        k: {
            "clip_count": len(v),
            "total_duration_sec": round(sum(c["duration_sec"] for c in v), 2),
            "is_synthetic_regression": any(bool(c.get("is_synthetic_regression")) for c in v),
        }
        for k, v in tracks.items()
    }

    manifest = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "db_sha256": db_hash_before,
        "track_summary": summary,
        "tracks": tracks,
        "full_meetings": {
            "m234": {"ref_path": str(M234_REF_PATH), "duration_sec": 90.15, **m234_full},
            "m235": {"ref_path": str(M235_REF_PATH), "duration_sec": 1129.0, **m235_full},
        },
        "continuous_streams": {
            "earnings22_us_continuous": e22_cont,
            "m240_us_in_call_5m": m240_cont,
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    db_hash_after = compute_target_db_rows_sha256()
    if db_hash_after != EXPECTED_DB_HASH:
        raise RuntimeError(f"DB hash changed during corpus prep: {db_hash_after}")

    print("\n=== Phase 1 Corpus Summary ===")
    for k, s in summary.items():
        print(f"  {k:22s}: {s['clip_count']:3d} clips | {s['total_duration_sec']:6.1f}s | synthetic={s['is_synthetic_regression']}")
    print(f"  earnings22_us_continuous: {e22_cont['duration_sec']:.1f}s ({e22_cont['clip_count']} segments)")
    print(f"  m240_us_in_call_5m     : {m240_cont['duration_sec']:.1f}s")
    print(f"  DB SHA-256 unchanged    : {db_hash_after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

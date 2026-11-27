"""
Phase 1.5 Benchmark Corpus Expansion (Strictly Read-Only on Production Data).

Extends `data/benchmarks/phase1_corpus/manifest.json` into
`data/benchmarks/phase15_corpus/manifest.json` by adding three real-world,
human-transcribed datasets:
1. `ami_meetings_en` (+ `ami_meetings_continuous` stream): Real multi-speaker
   business meetings from `edinburghcstr/ami` (`ihm/test`).
2. `monsoon_in_en`: Conversational Indian English across diverse Indian states
   from `VoiceArena/MonsoonASR-Open-ASR-leaderboard-en-IN`.
3. `coshe_hinglish`: Independent human-transcribed Hinglish (mixed Hindi + English)
   evaluation clips from `soketlabs/CoSHE-Eval`.

Strictly read-only on `src/voxlocal/`, `tests/`, recordings, and `data/voxlocal.db`.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"
for _p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from prepare_phase1_corpus import (
    EXPECTED_DB_HASH,
    CORPUS_DIR as PHASE1_CORPUS_DIR,
    MANIFEST_PATH as PHASE1_MANIFEST_PATH,
    _http_get_bytes,
    _http_get_json,
    decode_bytes_to_16k_mono_pcm16,
    validate_wav,
    write_pcm16_wav,
)
from benchmark_speech_intelligence import compute_target_db_rows_sha256
from voxlocal.audio.devices import TARGET_SAMPLE_RATE
from voxlocal.engine.dictation_processor import DictationProcessor

CORPUS15_DIR = ROOT_DIR / "data" / "benchmarks" / "phase15_corpus"
WAVS15_DIR = CORPUS15_DIR / "wavs"
CONT15_DIR = CORPUS15_DIR / "continuous"
MANIFEST15_PATH = CORPUS15_DIR / "manifest.json"

_BAD_MARK_RE = re.compile(r"[<>\[\]{}*_]|https?://", re.IGNORECASE)
_BACKCHANNEL_SET = {
    "yeah", "yes", "no", "mm", "mm-hmm", "uh-huh", "um", "uh", "okay", "ok",
    "right", "hmm", "ah", "oh", "yep", "nope", "mhm",
}


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


def _is_clean_en_text(text: str, min_words: int = 6, max_words: int = 45) -> bool:
    t = _clean_text(text)
    if not t or _BAD_MARK_RE.search(t):
        return False
    words = t.split()
    if len(words) < min_words or len(words) > max_words:
        return False
    low_words = [w.strip(".,!?;:\"'").lower() for w in words]
    non_bc = [w for w in low_words if w and w not in _BACKCHANNEL_SET]
    return len(non_bc) >= max(4, min_words - 1)


def _fetch_ds_json(url: str, retries: int = 5) -> Optional[Dict[str, Any]]:
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            return _http_get_json(url)
        except Exception as exc:
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    print(f"  [WARN] Giving up on {url}: {last_err}")
    return None


def _download_clip_wav(audio_entry: Any, wav_path: Path) -> Dict[str, Any]:
    if wav_path.exists():
        try:
            meta = validate_wav(wav_path)
            if meta["duration_sec"] >= 0.5:
                return meta
        except Exception:
            pass
    if isinstance(audio_entry, list) and audio_entry:
        src_url = audio_entry[0].get("src")
    elif isinstance(audio_entry, dict):
        src_url = audio_entry.get("src")
    else:
        src_url = None
    if not src_url:
        raise RuntimeError(f"Missing audio src URL for {wav_path.name}")
    raw = _http_get_bytes(src_url, timeout=45, retries=5)
    pcm16 = decode_bytes_to_16k_mono_pcm16(raw)
    return write_pcm16_wav(wav_path, pcm16, sample_rate=TARGET_SAMPLE_RATE)


def _norm_ref_case(txt: str) -> str:
    """Convert ALL-CAPS dataset text like AMI to clean readable casing while keeping acronyms readable."""
    t = _clean_text(txt)
    if t.isupper():
        # Sentence capitalization works cleanly with strict lowercasing scorer and human readability
        t = t.lower()
        t = t[0].upper() + t[1:]
        t = re.sub(r"\bi\b", "I", t)
        t = re.sub(r"\bi'([a-z]+)", lambda m: f"I'{m.group(1)}", t)
    return t


def prepare_ami_meetings(target_count: int = 40) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    print("[Corpus 1.5] Fetching AMI Multi-Speaker Business Meetings (edinburghcstr/ami ihm/test)...")
    out_dir = WAVS15_DIR / "ami_meetings_en"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Stride across 12,643 test rows so we sample multiple different meetings and speakers
    offsets = [0, 300, 700, 1200, 1800, 2500, 3300, 4200, 5200, 6500, 7800, 9200, 10800, 12000]
    candidates: List[Dict[str, Any]] = []
    seen_ids = set()
    for off in offsets:
        url = (
            "https://datasets-server.huggingface.co/rows"
            f"?dataset=edinburghcstr/ami&config=ihm&split=test&offset={off}&length=100"
        )
        d = _fetch_ds_json(url, retries=4)
        if not d:
            continue
        for item in d.get("rows", []):
            row = item.get("row") or {}
            aid = str(row.get("audio_id") or "")
            if not aid or aid in seen_ids:
                continue
            seen_ids.add(aid)
            b_t = float(row.get("begin_time") or 0.0)
            e_t = float(row.get("end_time") or 0.0)
            dur = e_t - b_t
            txt = str(row.get("text") or "")
            if dur < 3.0 or dur > 18.0:
                continue
            if not _is_clean_en_text(txt, min_words=7, max_words=45):
                continue
            candidates.append(row)

    # Sort deterministically while maximizing meeting_id + speaker diversity
    by_meeting: Dict[str, List[Dict[str, Any]]] = {}
    for row in candidates:
        by_meeting.setdefault(str(row.get("meeting_id") or "unk"), []).append(row)

    picked_rows: List[Dict[str, Any]] = []
    meeting_keys = sorted(by_meeting.keys())
    idx_per_m = {k: 0 for k in meeting_keys}
    used_spk_counts: Dict[str, int] = {}
    while len(picked_rows) < target_count and meeting_keys:
        progressed = False
        for mk in list(meeting_keys):
            pool = by_meeting[mk]
            pos = idx_per_m[mk]
            while pos < len(pool):
                cand = pool[pos]
                pos += 1
                spk = str(cand.get("speaker_id") or "")
                if used_spk_counts.get(spk, 0) >= 3 and len(picked_rows) < target_count - 5:
                    continue
                picked_rows.append(cand)
                used_spk_counts[spk] = used_spk_counts.get(spk, 0) + 1
                progressed = True
                break
            idx_per_m[mk] = pos
            if len(picked_rows) >= target_count:
                break
        if not progressed:
            break

    clips: List[Dict[str, Any]] = []
    cont_arrays: List[np.ndarray] = []
    cont_refs: List[str] = []
    cont_segments: List[Dict[str, Any]] = []
    cont_cursor = 0.0
    silence_gap = np.zeros(int(0.5 * TARGET_SAMPLE_RATE), dtype=np.int16)

    for i, row in enumerate(picked_rows):
        aid = str(row["audio_id"])
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", aid)
        wav_path = out_dir / f"ami_{safe_id}.wav"
        try:
            meta = _download_clip_wav(row.get("audio"), wav_path)
        except Exception as exc:
            print(f"  [WARN] AMI clip {aid} download failed: {exc}")
            continue
        if meta["duration_sec"] < 2.2 or meta["rms"] < 0.002:
            continue
        ref_txt = _norm_ref_case(str(row["text"]))
        clips.append({
            "id": f"ami_{safe_id}",
            "track": "ami_meetings_en",
            "language": "en",
            "accent": "en_multi_meeting",
            "meeting_id": str(row.get("meeting_id") or ""),
            "speaker_id": str(row.get("speaker_id") or ""),
            "wav_path": str(wav_path),
            "reference_text": ref_txt,
            "duration_sec": meta["duration_sec"],
            "rms": meta["rms"],
            "is_synthetic_regression": False,
        })
        if len(cont_segments) < 28 and cont_cursor < 210.0:
            import wave as _wave
            with _wave.open(str(wav_path), "rb") as wf:
                arr_i16 = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            seg_dur = arr_i16.size / float(TARGET_SAMPLE_RATE)
            cont_segments.append({
                "clip_id": f"ami_{safe_id}",
                "start_time": round(cont_cursor, 3),
                "end_time": round(cont_cursor + seg_dur, 3),
                "reference_text": ref_txt,
                "speaker_id": str(row.get("speaker_id") or ""),
            })
            cont_arrays.append(arr_i16)
            cont_arrays.append(silence_gap)
            cont_refs.append(ref_txt)
            cont_cursor += seg_dur + 0.5
        if len(clips) >= target_count:
            break

    CONT15_DIR.mkdir(parents=True, exist_ok=True)
    cont_wav = CONT15_DIR / "ami_meetings_continuous.wav"
    if cont_arrays:
        joined = np.concatenate(cont_arrays, axis=0)
        cont_meta = write_pcm16_wav(cont_wav, joined, sample_rate=TARGET_SAMPLE_RATE)
    else:
        cont_meta = {"duration_sec": 0.0, "rms": 0.0}

    cont_info = {
        "id": "ami_meetings_continuous",
        "wav_path": str(cont_wav),
        "duration_sec": cont_meta["duration_sec"],
        "rms": cont_meta["rms"],
        "clip_count": len(cont_segments),
        "reference_text": " ".join(cont_refs).strip(),
        "segments": cont_segments,
    }
    n_mtgs = len({c["meeting_id"] for c in clips})
    n_spks = len({c["speaker_id"] for c in clips})
    print(f"  [OK] ami_meetings_en: {len(clips)} clips ({sum(c['duration_sec'] for c in clips):.1f}s) across {n_mtgs} meetings, {n_spks} speakers")
    print(f"  [OK] ami_meetings_continuous: {cont_info['duration_sec']:.1f}s ({cont_info['clip_count']} segments)")
    return clips, cont_info


def prepare_monsoon_in_en(target_count: int = 40) -> List[Dict[str, Any]]:
    print("[Corpus 1.5] Fetching Conversational Indian English (VoiceArena/MonsoonASR-Open-ASR-leaderboard-en-IN)...")
    out_dir = WAVS15_DIR / "monsoon_in_en"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows_pool: List[Dict[str, Any]] = []
    # Use /first-rows first (verified stable), then /rows only if needed
    d0 = _fetch_ds_json(
        "https://datasets-server.huggingface.co/first-rows?dataset=VoiceArena/MonsoonASR-Open-ASR-leaderboard-en-IN&config=default&split=test",
        retries=5,
    )
    if d0:
        rows_pool.extend(item.get("row") or {} for item in d0.get("rows", []))

    if len(rows_pool) < 100:
        for off in (100, 200):
            d_extra = _fetch_ds_json(
                f"https://datasets-server.huggingface.co/rows?dataset=VoiceArena/MonsoonASR-Open-ASR-leaderboard-en-IN&config=default&split=test&offset={off}&length=100",
                retries=3,
            )
            if d_extra:
                rows_pool.extend(item.get("row") or {} for item in d_extra.get("rows", []))

    seen = set()
    cands: List[Dict[str, Any]] = []
    for row in rows_pool:
        rid = str(row.get("id") or "")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        dur = float(row.get("audio_length_s") or 0.0)
        txt = _clean_text(str(row.get("text") or ""))
        if dur < 2.5 or dur > 20.0:
            continue
        if not _is_clean_en_text(txt, min_words=5, max_words=55):
            continue
        cands.append(row)

    # Balance across states and speakers (max 2 per speaker)
    by_state: Dict[str, List[Dict[str, Any]]] = {}
    for r in cands:
        by_state.setdefault(str(r.get("native_state") or "unknown"), []).append(r)

    picked: List[Dict[str, Any]] = []
    spk_used: Dict[str, int] = {}
    state_keys = sorted(by_state.keys())
    pos_map = {k: 0 for k in state_keys}
    for cap in (1, 2, 4):
        while len(picked) < target_count:
            progressed = False
            for st in state_keys:
                pool = by_state[st]
                pos = pos_map[st]
                while pos < len(pool):
                    r = pool[pos]
                    pos += 1
                    sp = str(r.get("speaker_id") or "")
                    if spk_used.get(sp, 0) >= cap:
                        continue
                    picked.append(r)
                    spk_used[sp] = spk_used.get(sp, 0) + 1
                    progressed = True
                    break
                pos_map[st] = pos
                if len(picked) >= target_count:
                    break
            if not progressed:
                break

    clips: List[Dict[str, Any]] = []
    for row in picked:
        rid = str(row["id"])
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", rid)
        wav_path = out_dir / f"monsoon_{safe_id}.wav"
        try:
            meta = _download_clip_wav(row.get("audio"), wav_path)
        except Exception as exc:
            print(f"  [WARN] Monsoon clip {rid} download failed: {exc}")
            continue
        if meta["duration_sec"] < 2.0 or meta["rms"] < 0.002:
            continue
        ref_txt = _clean_text(str(row["text"]))
        clips.append({
            "id": f"monsoon_{safe_id}",
            "track": "monsoon_in_en",
            "language": "en",
            "accent": "en_in_conversational",
            "speaker_id": str(row.get("speaker_id") or ""),
            "native_state": str(row.get("native_state") or ""),
            "wav_path": str(wav_path),
            "reference_text": ref_txt,
            "duration_sec": meta["duration_sec"],
            "rms": meta["rms"],
            "is_synthetic_regression": False,
        })
        if len(clips) >= target_count:
            break

    n_states = len({c["native_state"] for c in clips})
    n_spks = len({c["speaker_id"] for c in clips})
    print(f"  [OK] monsoon_in_en: {len(clips)} clips ({sum(c['duration_sec'] for c in clips):.1f}s) across {n_states} states, {n_spks} speakers")
    return clips


def _has_both_devanagari_and_latin(text: str) -> bool:
    has_deva = bool(re.search(r"[\u0900-\u097F]", text))
    has_lat = bool(re.search(r"[A-Za-z]{2,}", text))
    return has_deva and has_lat


def prepare_coshe_hinglish(target_count: int = 30) -> List[Dict[str, Any]]:
    print("[Corpus 1.5] Fetching Independent Hinglish Code-Switching (Trelis/CoSHE-500)...", flush=True)
    out_dir = WAVS15_DIR / "coshe_hinglish"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Remove any partial files not matching coshe500_*
    for old_f in out_dir.glob("*.wav"):
        if not old_f.name.startswith("coshe500_"):
            old_f.unlink()

    d0 = _fetch_ds_json(
        "https://datasets-server.huggingface.co/first-rows?dataset=Trelis/CoSHE-500&config=default&split=eval",
        retries=5,
    )
    rows_pool: List[Dict[str, Any]] = []
    if d0:
        rows_pool.extend(item.get("row") or {} for item in d0.get("rows", []))

    cands_mixed: List[Tuple[int, Dict[str, Any]]] = []
    cands_other: List[Tuple[int, Dict[str, Any]]] = []
    seen_txt = set()
    for idx, row in enumerate(rows_pool):
        txt = _clean_text(str(row.get("transcription") or ""))
        if not txt or txt in seen_txt:
            continue
        seen_txt.add(txt)
        words = txt.split()
        if len(words) < 10 or len(words) > 95:
            continue
        if _BAD_MARK_RE.search(txt):
            continue
        if _has_both_devanagari_and_latin(txt):
            cands_mixed.append((idx, row))
        else:
            cands_other.append((idx, row))

    # Prioritize clips with >=15% Latin English mix first for balanced code-switch coverage
    def _mix_key(item: Tuple[int, Dict[str, Any]]) -> Tuple[int, int]:
        t = _clean_text(str(item[1].get("transcription") or ""))
        ws = t.split()
        lat_n = sum(1 for w in ws if re.search(r"[A-Za-z]{2,}", w))
        return (-abs(len(ws) // 3 - lat_n), len(ws))

    cands_mixed.sort(key=_mix_key, reverse=True)
    ordered = cands_mixed + cands_other
    clips: List[Dict[str, Any]] = []
    for idx, row in ordered:
        wav_path = out_dir / f"coshe500_{idx:03d}.wav"
        try:
            meta = _download_clip_wav(row.get("audio"), wav_path)
        except Exception as exc:
            print(f"  [WARN] CoSHE-500 row {idx} download failed: {exc}", flush=True)
            continue
        if meta["duration_sec"] < 3.0 or meta["duration_sec"] > 26.5 or meta["rms"] < 0.002:
            if wav_path.exists():
                wav_path.unlink()
            continue
        raw_txt = _clean_text(str(row["transcription"]))
        roman_ref = DictationProcessor.devanagari_to_roman_hinglish(raw_txt).strip()
        if not roman_ref:
            continue
        clips.append({
            "id": f"coshe500_{idx:03d}",
            "track": "coshe_hinglish",
            "language": "hinglish",
            "accent": "hinglish_coshe",
            "source_row_idx": idx,
            "has_devanagari_and_latin": _has_both_devanagari_and_latin(raw_txt),
            "wav_path": str(wav_path),
            "reference_raw": raw_txt,
            "reference_text": roman_ref,
            "duration_sec": meta["duration_sec"],
            "rms": meta["rms"],
            "is_synthetic_regression": False,
        })
        if len(clips) >= target_count:
            break

    cs_cnt = sum(1 for c in clips if c["has_devanagari_and_latin"])
    print(f"  [OK] coshe_hinglish: {len(clips)} clips ({sum(c['duration_sec'] for c in clips):.1f}s, {cs_cnt} mixed Deva+Latin)", flush=True)
    return clips


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    db_before = compute_target_db_rows_sha256()
    if db_before != EXPECTED_DB_HASH:
        raise RuntimeError(f"DB hash mismatch before Phase 1.5 corpus prep: {db_before}")
    if not PHASE1_MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Phase 1 manifest missing: {PHASE1_MANIFEST_PATH}")

    p1 = json.loads(PHASE1_MANIFEST_PATH.read_text(encoding="utf-8"))
    CORPUS15_DIR.mkdir(parents=True, exist_ok=True)
    WAVS15_DIR.mkdir(parents=True, exist_ok=True)
    CONT15_DIR.mkdir(parents=True, exist_ok=True)

    ami_clips, ami_cont = prepare_ami_meetings(target_count=40)
    monsoon_clips = prepare_monsoon_in_en(target_count=40)
    coshe_clips = prepare_coshe_hinglish(target_count=30)

    if len(ami_clips) < 35:
        raise RuntimeError(f"AMI yielded only {len(ami_clips)} clips (< 35)")
    if len(monsoon_clips) < 25:
        raise RuntimeError(f"MonsoonASR yielded only {len(monsoon_clips)} clips (< 25)")
    if len(coshe_clips) < 25:
        raise RuntimeError(f"CoSHE-Eval yielded only {len(coshe_clips)} clips (< 25)")

    tracks: Dict[str, List[Dict[str, Any]]] = dict(p1["tracks"])
    tracks["ami_meetings_en"] = ami_clips
    tracks["monsoon_in_en"] = monsoon_clips
    tracks["coshe_hinglish"] = coshe_clips

    summary = {
        k: {
            "clip_count": len(v),
            "total_duration_sec": round(sum(float(c["duration_sec"]) for c in v), 2),
            "is_synthetic_regression": any(bool(c.get("is_synthetic_regression")) for c in v),
        }
        for k, v in tracks.items()
    }

    continuous = dict(p1.get("continuous_streams") or {})
    continuous["ami_meetings_continuous"] = ami_cont

    manifest15 = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "db_sha256": db_before,
        "base_phase1_manifest": str(PHASE1_MANIFEST_PATH),
        "track_summary": summary,
        "tracks": tracks,
        "full_meetings": dict(p1.get("full_meetings") or {}),
        "continuous_streams": continuous,
    }
    MANIFEST15_PATH.write_text(json.dumps(manifest15, indent=2, ensure_ascii=False), encoding="utf-8")

    db_after = compute_target_db_rows_sha256()
    if db_after != EXPECTED_DB_HASH:
        raise RuntimeError(f"DB hash changed during Phase 1.5 corpus prep: {db_after}")

    print("\n=== Phase 1.5 Expanded Corpus Summary ===")
    for k, s in summary.items():
        print(f"  {k:22s}: {s['clip_count']:3d} clips | {s['total_duration_sec']:6.1f}s | synthetic={s['is_synthetic_regression']}")
    print(f"  ami_meetings_continuous : {ami_cont['duration_sec']:.1f}s ({ami_cont['clip_count']} segments)")
    print(f"  DB SHA-256 unchanged    : {db_after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

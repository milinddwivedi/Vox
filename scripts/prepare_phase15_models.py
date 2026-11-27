"""
Phase 1.5 Challenger Model Preparation & Mode Probe (Strictly Read-Only on Production Data).

Downloads and converts:
1. `abhishekgautamm/whisper-base-hinglish` -> `data/models/challengers/whisper-base-hinglish-ct2-int8/`
2. `Oriserve/Whisper-Hindi2Hinglish-Apex` -> HF snapshot + CTranslate2 INT8 conversion in
   `data/models/challengers/oriserve-apex-ct2-int8/`

Runs a 4-clip language-mode probe (`language=None` vs `language="hi"`) for Oriserve Apex and
a smoke-load check for Whisper Base Hinglish, saving results to
`data/benchmarks/phase15_results/oriserve_apex_mode_probe.json`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from huggingface_hub import HfApi, snapshot_download

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"
for _p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from benchmark_speech_intelligence import compute_target_db_rows_sha256
from voxlocal.engine.transcriber import load_wav_as_16k_f32

EXPECTED_DB_HASH = "e632521df929d06ce9da552a2015cab96fe6e0a3c1e00200b1673c12ebb57439"
CHALLENGERS_DIR = ROOT_DIR / "data" / "models" / "challengers"
CONVERTER_EXE = ROOT_DIR / ".venv" / "Scripts" / "ct2-transformers-converter.EXE"
RESULTS_DIR = ROOT_DIR / "data" / "benchmarks" / "phase15_results"
CORPUS1_MANIFEST = ROOT_DIR / "data" / "benchmarks" / "phase1_corpus" / "manifest.json"
CORPUS15_MANIFEST = ROOT_DIR / "data" / "benchmarks" / "phase15_corpus" / "manifest.json"


def prepare_whisper_base_hinglish() -> Path:
    repo_id = "abhishekgautamm/whisper-base-hinglish"
    out_dir = CHALLENGERS_DIR / "whisper-base-hinglish-ct2-int8"
    out_dir.mkdir(parents=True, exist_ok=True)
    if (out_dir / "model.bin").exists() and (out_dir / "config.json").exists():
        print(f"[Model] whisper-base-hinglish CT2 INT8 already present at {out_dir}")
        return out_dir

    print(f"[Model] Inspecting {repo_id} files...")
    api = HfApi()
    files = api.list_repo_files(repo_id)
    ct2_files = [f for f in files if f.startswith("ct2/int8/")]
    needed_top = [f for f in ("config.json", "preprocessor_config.json", "tokenizer.json", "vocabulary.json", "vocabulary.txt", "tokenizer_config.json", "special_tokens_map.json") if f in files]
    patterns = sorted(set(ct2_files + needed_top))

    tmp_snap = Path(
        snapshot_download(
            repo_id=repo_id,
            allow_patterns=patterns,
        )
    )
    for rel in ct2_files:
        src = tmp_snap / rel
        if src.is_file():
            shutil.copy2(src, out_dir / src.name)
    for rel in needed_top:
        dst = out_dir / Path(rel).name
        src = tmp_snap / rel
        if src.is_file() and not dst.exists():
            shutil.copy2(src, dst)

    if not (out_dir / "model.bin").exists():
        raise RuntimeError(f"Failed locating model.bin in {out_dir} after downloading {repo_id}")
    print(f"  [OK] whisper-base-hinglish ready ({(out_dir / 'model.bin').stat().st_size / (1024**2):.1f} MB)")
    return out_dir


def prepare_oriserve_apex() -> Path:
    repo_id = "Oriserve/Whisper-Hindi2Hinglish-Apex"
    hf_dir = CHALLENGERS_DIR / "oriserve-apex-hf"
    ct2_dir = CHALLENGERS_DIR / "oriserve-apex-ct2-int8"
    if (ct2_dir / "model.bin").exists() and (ct2_dir / "config.json").exists():
        print(f"[Model] oriserve-apex CT2 INT8 already present at {ct2_dir}")
        return ct2_dir

    print(f"[Model] Downloading {repo_id} -> {hf_dir}...")
    hf_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(hf_dir),
    )

    copy_candidates = [
        f
        for f in ("tokenizer.json", "preprocessor_config.json", "tokenizer_config.json", "special_tokens_map.json")
        if (hf_dir / f).exists()
    ]
    if not CONVERTER_EXE.exists():
        raise FileNotFoundError(f"Converter executable missing: {CONVERTER_EXE}")

    cmd = [
        str(CONVERTER_EXE),
        "--model",
        str(hf_dir),
        "--output_dir",
        str(ct2_dir),
        "--quantization",
        "int8",
        "--force",
    ]
    if copy_candidates:
        cmd.append("--copy_files")
        cmd.extend(copy_candidates)

    print(f"[Model] Converting Oriserve Apex to CTranslate2 INT8...")
    res = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True)
    if res.returncode != 0:
        print("STDOUT:", res.stdout)
        print("STDERR:", res.stderr)
        raise RuntimeError(f"ct2-transformers-converter failed with exit {res.returncode}")

    # Copy tokenizer/preprocessor files defensively if converter skipped any
    for fname in copy_candidates + ["generation_config.json"]:
        src = hf_dir / fname
        dst = ct2_dir / fname
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)

    if not (ct2_dir / "model.bin").exists():
        raise RuntimeError(f"Missing converted model.bin at {ct2_dir}")
    print(f"  [OK] oriserve-apex CT2 INT8 ready ({(ct2_dir / 'model.bin').stat().st_size / (1024**2):.1f} MB)")
    return ct2_dir


def run_probes(base_dir: Path, apex_dir: Path) -> None:
    from faster_whisper import WhisperModel

    mpath = CORPUS15_MANIFEST if CORPUS15_MANIFEST.exists() else CORPUS1_MANIFEST
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    probe_clips: List[Dict[str, Any]] = [
        manifest["tracks"]["earnings22_us"][0],
        manifest["tracks"]["fleurs_hi_in"][0],
        manifest["tracks"]["m234_full_90s"][0],
    ]
    if "coshe_hinglish" in manifest["tracks"] and manifest["tracks"]["coshe_hinglish"]:
        probe_clips.append(manifest["tracks"]["coshe_hinglish"][0])
    if "ami_meetings_en" in manifest["tracks"] and manifest["tracks"]["ami_meetings_en"]:
        probe_clips.append(manifest["tracks"]["ami_meetings_en"][0])

    probe_out: Dict[str, Any] = {"generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "models": {}}

    # 1. Smoke test whisper-base-hinglish
    print("\n[Probe] whisper-base-hinglish (CPU INT8)...")
    m_base = WhisperModel(str(base_dir), device="cpu", compute_type="int8", cpu_threads=6)
    base_res = []
    for clip in probe_clips:
        arr = load_wav_as_16k_f32(clip["wav_path"])
        t0 = time.perf_counter()
        segs, info = m_base.transcribe(arr, language=None, beam_size=2, vad_filter=False, condition_on_previous_text=False)
        seg_list = list(segs)
        dt = time.perf_counter() - t0
        txt = " ".join(s.text.strip() for s in seg_list if s.text.strip()).strip()
        ref = clip.get("reference_text") or clip.get("reference_roman") or ""
        print(f"  [base lang={info.language}] {clip['id']}: RTF={dt/clip['duration_sec']:.3f}x | HYP={txt!r} | REF={ref!r}")
        base_res.append({"id": clip["id"], "track": clip["track"], "det_lang": info.language, "rtf": round(dt / clip["duration_sec"], 3), "hyp": txt, "ref": ref})
    probe_out["models"]["whisper_base_hinglish"] = base_res
    del m_base

    # 2. Mode probe Oriserve Apex: language=None vs language="hi" vs language="en"
    print("\n[Probe] Oriserve/Whisper-Hindi2Hinglish-Apex (CPU INT8, language=None vs hi)...")
    m_apex = WhisperModel(str(apex_dir), device="cpu", compute_type="int8", cpu_threads=8)
    apex_res: Dict[str, Any] = {"lang_auto": [], "lang_hi": []}
    for mode_key, lang_arg in (("lang_auto", None), ("lang_hi", "hi")):
        print(f"  -- Oriserve Apex language={lang_arg!r} --")
        for clip in probe_clips:
            arr = load_wav_as_16k_f32(clip["wav_path"])
            t0 = time.perf_counter()
            segs, info = m_apex.transcribe(arr, language=lang_arg, beam_size=2, vad_filter=False, condition_on_previous_text=False)
            seg_list = list(segs)
            dt = time.perf_counter() - t0
            txt = " ".join(s.text.strip() for s in seg_list if s.text.strip()).strip()
            ref = clip.get("reference_text") or clip.get("reference_roman") or ""
            print(f"    [{mode_key} det={info.language}] {clip['id']}: RTF={dt/clip['duration_sec']:.3f}x | HYP={txt!r} | REF={ref!r}")
            apex_res[mode_key].append({
                "id": clip["id"],
                "track": clip["track"],
                "det_lang": info.language,
                "rtf": round(dt / clip["duration_sec"], 3),
                "hyp": txt,
                "ref": ref,
            })
    probe_out["models"]["oriserve_apex_ct2_int8"] = apex_res
    del m_apex

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = RESULTS_DIR / "oriserve_apex_mode_probe.json"
    out_file.write_text(json.dumps(probe_out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[DONE] Saved mode probe to {out_file}")


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    db_before = compute_target_db_rows_sha256()
    if db_before != EXPECTED_DB_HASH:
        raise RuntimeError(f"DB hash mismatch: {db_before}")
    base_dir = prepare_whisper_base_hinglish()
    apex_dir = prepare_oriserve_apex()
    run_probes(base_dir, apex_dir)
    db_after = compute_target_db_rows_sha256()
    if db_after != EXPECTED_DB_HASH:
        raise RuntimeError(f"DB hash changed: {db_after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

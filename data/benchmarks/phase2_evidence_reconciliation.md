# VOX Phase 2 — Phase 1.5 Evidence Reconciliation

- Reconciled directly from raw artifacts:
  - `data/benchmarks/phase15_results/phase15_validation_report.json` (`2026-11-23T06:01:41Z`, `db_sha256=e632521df929d06ce9da552a2015cab96fe6e0a3c1e00200b1673c12ebb57439`)
  - `data/benchmarks/phase15_results/stream_v2_results.json`
  - `data/benchmarks/phase15_results/routing_v2_results.json`
  - `data/benchmarks/phase15_results/runs` -> `data/benchmarks/phase15_results/onnx_qwen3_asr_06b_int8_guarded.json`
- Locked Ground Truth Meetings:
  - **M#234** (`Hemanth Kumar Talathoti`): **16 / 16 segments human-verified** (100% locked ground truth).
  - **M#235** (`Rumjhum`): **111 / 111 segments human-verified** (100% locked ground truth).
  - **M#240**: Unverified draft (0 / 26 verified) — **excluded from accuracy ground truth**; used only for 5-minute shared-queue runtime/latency/drop/script-purity stress telemetry.

---

## 1. Streaming Capability Taxonomy (No Overclaiming)

| Engine / Policy | Hardware | Streaming Classification | Dual-Track Shared-Queue Telemetry (`M#234` 90.15s) | `E22 US` Continuous | `AMI` Continuous | Production Role |
|---|---|---|---|---|---|---|
| `nemotron_560ms_shared_q` (`frame_560`) | CPU INT8 (0 VRAM) | **True frame-incremental streaming** (`is_word_by_word_incremental=True`) | `0.28x` RTF, P50 1st word `1.81s`, P95 `2.59s`, P50 commit `1.10s`, max backlog `0.17s`, `0` drops | `28.18%` WER, `0.234x` RTF, max BL `0.17s`, `0` drops | `14.14%` WER, `0.227x` RTF, max BL `0.11s`, `0` drops | **Live English incremental streaming** (`incremental` UI badge) |
| `vad_parakeet_tdt_v2_cpu` (`vad`) | CPU INT8 (0 VRAM) | **Endpointed (NOT word-by-word incremental)** (`is_word_by_word_incremental=False`) | `0.11x` RTF, P50 `0.30s` (at turn end), P95 `0.97s`, max backlog `0.00s`, `0` drops, `80.18%` EN-only WER on Hinglish `M#234` | `25.34%` WER, `0.072x` RTF, max BL `0.00s`, `0` drops | `6.87%` WER, `0.072x` RTF, max BL `0.00s`, `0` drops | Fast CPU offline English finalizer & fallback CPU English live endpointed engine |
| `vad_qwen3_onnx_guarded_cpu` (`vad`) | CPU INT8 (0 VRAM) | **Endpointed (NOT word-by-word incremental)** (`is_word_by_word_incremental=False`) | `0.83x` RTF, P50 turn-end `8.51s`, P95 `10.47s`, max backlog `8.53s`, `0` drops, `45.12%` official Hinglish WER | `25.96%` WER, `0.248x` RTF, max BL `1.04s`, `0` drops | `15.15%` WER, `0.281x` RTF, max BL `4.40s`, `0` drops | **Live Hindi/Hinglish/Auto endpointed engine** (0 VRAM, explicitly labeled `endpointed` in UI & telemetry) |
| `la2_whisper_base_hinglish_cpu` (`la2`) | CPU INT8 | LocalAgreement-2 incremental (Overflows dual-track) | `1.65x` RTF, max BL `62.87s`, `79` dropped chunks (>15s), `52.44%` WER | `50.68%` WER, `0.961x` RTF | `36.57%` WER, `0.990x` RTF | Rejected for production (real-time overflow) |
| `la2_qwen3_onnx_guarded_cpu` (`la2`) | CPU INT8 | LocalAgreement-2 incremental (Overflows) | `2.91x` RTF, max BL `179.16s`, `83` drops | `25.71%` WER, `1.375x` RTF, `209` drops | `15.76%` WER, `1.453x` RTF, `85` drops | Rejected for live incremental |
| `la2_whisper_small_cpu` (`la2`) | CPU INT8 | LocalAgreement-2 incremental (Severe overflow) | `6.05x` RTF, max BL `457.76s`, `105` drops | `39.06%` WER, `3.921x` RTF, `267` drops | `22.83%` WER, `3.757x` RTF, `144` drops | Rejected |
| `la2_oriserve_apex_cpu` (`la2`) | CPU INT8 | LocalAgreement-2 incremental (Extreme overflow) | `14.05x` RTF, max BL `1169.11s`, `90` drops | — | `36.57%` WER, `9.505x` RTF, `118` drops | Rejected for CPU live/incremental |

**Production Integrity Rule:** Only `nemotron_560ms` is exposed as `execution_mode = "incremental"` (`is_incremental = True`). `Guarded Qwen3 ONNX` and `Parakeet TDT v2` are strictly labeled `execution_mode = "endpointed"` (`is_incremental = False`).

---

## 2. Exact Metric Reconciliation (Single Models, English vs Hindi/Hinglish, Clip vs Official Stream, Raw vs Normalized)

### A. Single-Model Accuracy Matrix (Clip WER vs Official Meeting Stream WER)

| Model | Device / Compute | `e22_us` (Clip WER) | `fleurs_en` (Clip WER) | `ami_en` (Clip WER) | `nptel_in` (Clip WER) | `monsoon_in` (Clip WER) | `fleurs_hi` (Roman / Dev) | `coshe_mix` (Clip WER) | `M#234` Clip / Official Stream (Raw -> Norm) | `M#235` (111) Clip / Official Stream (Raw -> Norm) | Foreign Leaks (111) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `hf_qwen3_asr_06b_guarded` | CUDA FP16 (`3443 MB` pk VRAM, `3244 MB` RSS) | 23.11% | 4.80% | 9.62% | 22.97% | 4.60% | 19.09% / 23.05% | 26.66% | 34.45% / 40.85% -> **33.84%** | 37.25% / 44.10% -> **36.56%** | 0 |
| `onnx_qwen3_asr_06b_int8_guarded` | CPU INT8 (0 VRAM, `3499 MB` RSS) | 27.44% | 7.09% | 12.50% | 25.47% | 6.13% | 20.26% / 24.80% | 38.44% | 42.38% / 49.39% -> **41.46%** | 46.33% / 52.72% -> **45.12%** | 0 |
| `onnx_parakeet_tdt_v2_int8` | CPU INT8 (0 VRAM, `1888 MB` RSS) | 22.99% | **4.46%** | **5.36%** | 23.75% | **4.48%** | 80.91% / 100% | 73.34% | 78.35% / 77.74% -> 77.74% | 81.90% / 80.56% -> 80.27% (34.5% omit) | 0 |
| `ct2_oriserve_apex_int8` (`lang=None`) | CUDA INT8 (`2326 MB` pk VRAM) | 27.07% | 9.71% | 26.10% | 42.19% | 7.78% | 27.82% / 102.8% | **25.42%** | 36.59% / 44.21% -> 35.67% | **34.80%** / 42.52% -> **33.73%** | 1 CJK |
| `ct2_oriserve_apex_cpu_rtf_check` | CPU INT8 (`1.70–2.27x` RTF) | — | — | 20.05% | — | — | — | — | 33.84% / 42.07% -> 32.93% | — | 0 |
| `prod_whisper_turbo_balanced` | CUDA INT8 (`2366 MB` pk VRAM) | 21.76% | 4.46% | 19.51% | 22.19% | 6.84% | 39.58% / 47.73% | 53.54% | 63.41% / 65.24% -> 62.20% | 59.80% / 62.64% -> 58.16% | 0 |
| `prod_whisper_small_balanced` | CPU INT8 (Current P0 fallback) | 27.81% | 14.29% | 24.04% | 31.09% | 21.34% | 56.81% / 65.89% | 70.82% | 80.49% / 80.79% -> 78.66% | 80.18% / 81.12% -> 79.02% | 1 Arab |

> **Key Discrepancies Explained:**
> 1. **Clip WER vs Official Stream WER:** Segment-level Clip WER (`phase15_validation_report.json` `tracks.*`) aligns each ground-truth clip independently; Official Stream WER (`official_meetings.*.stream_hinglish_norm_wer`) joins segments chronologically across the meeting and aligns with jiwer continuous stream scoring, typically ~0.6–1.6% lower due to boundary alignment (`46.33% -> 45.12%` on `M#235` for Guarded Qwen3 ONNX; `37.25% -> 36.56%` for HF Guarded Qwen3 FP16).
> 2. **Raw vs Hinglish-Normalized WER:** Official `stream_raw_wer` scores exact orthographic forms without Romanized Hinglish normalization (e.g., `hai`/`hain`, `mein`/`main`, `theek`/`thik`), adding ~6–8 percentage points over `stream_hinglish_norm_wer` (`49.39% -> 41.46%` on `M#234`, `52.72% -> 45.12%` on `M#235` for Guarded Qwen3 ONNX).
> 3. **Production Hotwords Impact (`p0` vs `prod_whisper_*`):** On English, production hotwords + `DictationProcessor` degraded Whisper Small on Indian English (`monsoon_in`: `10.26% -> 21.34%`) and `fleurs_en` (`5.71% -> 14.29%`). Guarded Qwen3 ONNX with hotwords slightly helped `m235_strat30` (`45.36% -> 43.99%`) and `e22` (`27.44% -> 26.82%`), but regressed `m234` (`42.38% -> 49.39%`).

### B. Routing & Selective Second-Pass Reconciliation (`routing_v2_results.json`)

| Strategy | Hardware Tier | `e22_us` | `ami_en` | `nptel_in` | `monsoon_in` | `fleurs_hi` | `coshe_mix` | `M#234` | `M#235 (111)` | `M#235` Escalated / Accepted |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `session_auto_parakeet_to_qwen3_onnx` | CPU 0-VRAM | 26.82% | 5.36% | 23.75% | 4.48% | 20.26% | 38.44% | 42.38% | 46.33% | 100% / 100% |
| `session_auto_parakeet_to_hf_qwen3` | CUDA GPU | 22.99% | 5.36% | 23.75% | 4.48% | 19.09% | 26.66% | 34.45% | 37.25% | 100% / 100% |
| `sel_qwen3_onnx_to_prod_turbo_lp45` | Hybrid | 27.44% | 12.50% | 25.47% | 6.13% | 20.26% | 38.44% | 42.38% | **47.27% (worse)** | 19.8% / 9.0% |
| `sel_qwen3_onnx_to_oriserve_apex_lp45_hicue` | CUDA >= 1100 MB | 27.69% (7.5% esc) | 12.50% | 25.47% | 6.13% | 27.82% | **26.54%** | **39.94%** | **35.97%** | 83.8% / 72.1% |
| `sel_hf_qwen3_to_oriserve_apex_lp45_hicue` | CUDA >= 2000 MB | 23.49% (2.5% esc) | 9.62% | 22.97% | 4.60% | 27.82% | **24.86%** | 35.67% | **35.36%** | 82.0% / 68.5% |
| `sel_prod_turbo_to_oriserve_apex_lp55_hicue` | CUDA >= 1100 MB | 22.62% | 17.31% | 22.50% | 6.84% | 30.85% | 28.84% | 37.20% | 36.19% | 90.1% / 78.4% |

---

## 3. Provisional Thresholds & Production Guardrails

1. **`avg_logprob < -0.45` is a PROVISIONAL configurable parameter (`AppConfig.selective_logprob_threshold`), NOT a proven universal threshold:**
   - Directly measured in `onnx_qwen3_asr_06b_int8_guarded.json`:
     - On all 160 Phase 1.5 English clips (`earnings22_us` 40, `fleurs_en_us` 40, `ami_meetings_en` 40, `nptel_in_en` 40, plus `monsoon_in_en` 40 = 200 clips + 12 neural clips), `detected_language == 'en'` on **100% of clips (212/212)** and `avg_logprob < -0.45` fired on **0 / 212 (0.0%)**! Median `avg_logprob` was `-0.045` to `-0.140` (`min = -0.344`).
     - On `M#235 (111)`, `avg_logprob < -0.45` alone fired on only **7 / 108 (6.5%)** scored clips (`min = -0.939`, median `-0.152`).
     - Escalating Qwen3 ONNX segments to `prod_whisper_turbo` on `LP < -0.45` did **not** improve Hinglish WER (`46.33% -> 47.27%`), because `prod_whisper_turbo` is weaker than Qwen3 ONNX on Hinglish!
     - Selective 2nd-pass only improves accuracy when the 2nd-pass verifier is empirically stronger for that utterance's language/failure mode (e.g., `Oriserve Apex CT2` CUDA on Hindi/Hinglish slices, or `Parakeet` / `Whisper Turbo` on English-only utterances).
2. **English Homographs Must Be Excluded from Hinglish Lexical Cues:**
   - In Phase 1.5 (`routing_v2_results.json`), including English homographs (`"to"`, `"me"`, `"par"`) in `HINDI_ROMAN_CUES` falsely escalated English clips (`earnings22_us`: `7.5%` on `sel_qwen3_onnx_to_oriserve_apex`, `92.5%` in session-level auto-escalate trigger). Production Hinglish cue detection MUST require `Devanagari` / `detected_language == "hi"` / `>= 2` unambiguous Romanized Hindi tokens and exclude English homographs (`to`, `me`, `par`, `in`, `on`, `at`, `or`, `so`, `no`, `do`, `go`, `is`, `it`, `he`, `we`, `be`, `as`).
3. **Architecture Decision Summary for Production Code:**
   - **English session mode (`asr_session_mode="english"`):** Live = `Nemotron 560ms` (CPU INT8, `incremental`); Offline = `Parakeet TDT v2` (CPU INT8, `0.07–0.09x` RTF) or `Whisper Large-v3-Turbo` (CUDA/CPU); never route English to `Oriserve Apex`.
   - **Auto / Hinglish session mode (`asr_session_mode in ("auto", "hinglish")`):**
     - **CPU-only / <1100 MB free VRAM:** Live = `Guarded Qwen3 ONNX INT8` (CPU, 0 VRAM, labeled `endpointed`); Offline = `Guarded Qwen3 ONNX INT8` (CPU, 0 VRAM); no selective escalation to weaker `Whisper Small` or `Parakeet`!
     - **GPU with `>= 2000 MB` free VRAM:** Live = `Guarded Qwen3 ONNX INT8` (CPU, 0 VRAM, keeping GPU headroom free and avoiding GPU OOM during capture); Offline = `HF Guarded Qwen3 FP16` (CUDA) + optional selective `Oriserve Apex CT2` (CUDA, Hindi/Hinglish slices only).
     - **GPU with `1100–1999 MB` free VRAM:** Live = `Guarded Qwen3 ONNX INT8` (CPU, 0 VRAM); Offline = `Guarded Qwen3 ONNX INT8` + selective `Oriserve Apex CT2` (CUDA, Hindi/Hinglish slices only).

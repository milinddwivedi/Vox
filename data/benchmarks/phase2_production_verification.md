# VOX Phase 2 — Production Pipeline Benchmark Verification

- Generated UTC: `2026-11-23T08:54:56Z`
- Protected `data/voxlocal.db` SHA-256 unchanged: `e632521df929d06ce9da552a2015cab96fe6e0a3c1e00200b1673c12ebb57439`

## 1. Production Offline Engines (`src/voxlocal/engine/onnx_asr_engines.py`)

### A. `ParakeetOnnxEngine` (English CPU INT8, 0 VRAM) — RSS `1675.5 MB`, Avg CPU `440.2%`
| Track | Clip WER | RTF | Omit % |
|---|---:|---:|---:|
| `earnings22_us` | 23.61% | 0.075x | 0.0% |
| `fleurs_en_us` | 4.11% | 0.075x | 0.0% |
| `ami_meetings_en` | 5.63% | 0.081x | 0.0% |
| `nptel_in_en` | 23.13% | 0.072x | 0.0% |
| `monsoon_in_en` | 4.36% | 0.073x | 0.0% |

### B. `GuardedQwen3OnnxEngine` (Bilingual/Hinglish CPU INT8, 0 VRAM) — RSS `3620.0 MB`, Avg CPU `399.0%`
| Track | Clip WER | Official Stream Norm WER | RTF | Omit % | CJK/Arab/Kana/UFFFD Leaks |
|---|---:|---:|---:|---:|---|
| `monsoon_in_en` | 5.07% | — | 0.240x | 0.0% | 0/0/0/0 |
| `coshe_hinglish` | 40.57% | — | 0.806x | 0.0% | 0/0/0/0 |
| `m234_full_90s` | 48.78% | 47.56% | 0.578x | 0.0% | 0/0/0/0 |
| `m235_verified_111` | 45.88% | 44.73% | 0.457x | 0.0% | 0/0/0/0 |
> *Note on INT8 dynamic quantization ULP variance:* Feeding exact `Qwen3ASRFeatureExtractor` tensors directly into production `GuardedQwen3OnnxEngine` produces `42.38%` clip WER on `M#234` (0.00% delta vs Phase 1.5), while adding `+1e-6` perturbation shifts `M#234` to `43.90%` because `encoder.int8.onnx` uses dynamic INT8 quantization where `1e-5` log-mel precision flips quantization boundaries. Pure-NumPy maintains `< 1e-4` log-mel parity with zero PyTorch/Transformers dependency, improving `M#235` (111 clips) and `monsoon_in_en` while exhibiting higher variance on the small 16-clip `M#234` sample.

## 2. Production `SelectiveSecondPassVerifier` (`src/voxlocal/engine/selective_verifier.py`)
- VRAM before: `2557 MB` -> after `unload_model()`: `2639 MB`
| Track | Verified Clip WER | Escalated | Accepted | Guard-Skipped (EN Protect) |
|---|---:|---:|---:|---:|
| `m234_full_90s` | 36.28% | 10 (62.5%) | 9 | 2 |
| `m235_verified_111` | 36.25% | 81 (72.97%) | 77 | 13 |
| `monsoon_in_en` | 5.07% | 0 (0.0%) | 0 | 1 |

## 3. Production `LiveMeetingStreamingTranscriber` (`src/voxlocal/engine/streaming_transcriber.py`)
| Test Run | Execution Mode | `is_incremental` | Accuracy (WER) | RTF | 1st Partial | 1st Stable | EOT P50 / P95 | Max Backlog | Dropped Chunks |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `english_incremental_nemotron_ami` | `incremental` | `True` | 31.52% | 0.325x | 4412ms | 10128ms | 985ms / 2635ms | 0.56s | 0 |
| `hinglish_endpointed_qwen3_m234` | `endpointed` | `False` | 46.95% | 1.337x | — | 9380ms | 1906ms / 14789ms | 13.95s | 0 |

# VOX — Third-Party Software & AI Model Notices

VOX Voice Assistant runs 100% local AI inference on Windows. Below are the open-source libraries, runtime engines, fonts, and AI model weights used or provisioned by VOX, together with their respective licenses and attributions.

---

## 1. Local AI Model Weights

### Cohere Transcribe 2B (ONNX INT8, sherpa-onnx export)
- **Feature:** English post-call meeting transcription (`meeting_asr`)
- **Upstream Model:** `CohereLabs/cohere-transcribe-03-2026`
- **Packaged Distribution:** `csukuangfj2/sherpa-onnx-cohere-transcribe-14-lang-int8-2026-04-01`
- **License:** Apache License 2.0 (`Apache-2.0`)
- **Copyright:** Copyright © Cohere Labs / sherpa-onnx contributors
- **Notes:** Redistributed / provisioned unmodified from public Apache-2.0 release under Apache-2.0 terms.

### PyAnnote Segmentation 3.0 & WeSpeaker ResNet34-LM
- **Feature:** Local neural speaker diarization (`meeting_diarization`)
- **Upstream Components:**
  - `csukuangfj/sherpa-onnx-pyannote-segmentation-3-0` (converted from `pyannote/segmentation-3.0`)
  - `wespeaker_en_voxceleb_resnet34_LM.onnx` from WeSpeaker / k2-fsa `sherpa-onnx`
- **License:** MIT License (`pyannote-segmentation-3.0` / `sherpa-onnx`) & Apache License 2.0 (`WeSpeaker`, `sherpa-onnx`)
- **Copyright:** Copyright © Hervé Bredin (pyannote), WeSpeaker Authors, k2-fsa / Xiaomi Corp.

### Qwen3.5-4B (GGUF Q4_K_M)
- **Feature:** Local meeting intelligence, summaries, decisions, action items, knowledge extraction, and Ask VOX (`meeting_llm`)
- **Upstream Model:** Qwen3.5-4B (GGUF conversion `unsloth/Qwen3.5-4B-GGUF`)
- **License:** Apache License 2.0 (`Apache-2.0`)
- **Copyright:** Copyright © Alibaba Cloud / Qwen Team

### BGE-Small-EN v1.5 (ONNX INT8)
- **Feature:** 384-d dense embeddings for local semantic RAG & cross-meeting knowledge search (`meeting_embeddings`)
- **Upstream Model:** `BAAI/bge-small-en-v1.5` (`Qdrant/bge-small-en-v1.5-onnx-Q`)
- **License:** MIT License (`MIT`)
- **Copyright:** Copyright © Beijing Academy of Artificial Intelligence (BAAI) & Qdrant

### OpenAI Whisper (`large-v3-turbo` & `small`, CTranslate2 format)
- **Feature:** Universal Voice Dictation (`dictation_whisper_turbo`, `dictation_whisper_small`)
- **Upstream Model:** OpenAI Whisper (`deepdml/faster-whisper-large-v3-turbo-ct2`, `Systran/faster-whisper-small`)
- **License:** MIT License (`MIT`)
- **Copyright:** Copyright © OpenAI / Systran / Faster-Whisper Contributors

### Silero VAD v6
- **Feature:** Voice activity detection (`faster_whisper/assets/silero_vad_v6.onnx`)
- **License:** MIT License (`MIT`)
- **Copyright:** Copyright © Silero Team

---

## 2. Bundled Native Runtimes & Fonts

### llama.cpp (`llama-server.exe`, `ggml`, CPU & Vulkan backends)
- **License:** MIT License (`MIT`)
- **Copyright:** Copyright © Georgi Gerganov and The llama.cpp Authors
- **Bundled Binaries:** CPU and Vulkan runtime backends (`runtime/llama_cpp/`). CUDA toolkit is NOT required or bundled.

### sherpa-onnx & ONNX Runtime
- **sherpa-onnx:** Apache License 2.0 (`Apache-2.0`), Copyright © k2-fsa / Next-gen Kaldi Contributors
- **ONNX Runtime:** MIT License (`MIT`), Copyright © Microsoft Corporation

### Faster-Whisper & CTranslate2
- **Faster-Whisper:** MIT License (`MIT`), Copyright © Guillaume Klein / SYSTRAN
- **CTranslate2:** MIT License (`MIT`), Copyright © OpenNMT / CTranslate2 Contributors

### Qt / PyQt6 (`PyQt6`, `PyQt6-Qt6`, `PyQt6_sip`)
- **License:** GNU General Public License v3 (GPL-3.0) / Riverbank Commercial License (Qt Company LGPLv3/GPLv3/Commercial)
- **Notice:** VOX links dynamically against PyQt6-Qt6 shared libraries under `_internal/PyQt6/`.

### PyAV & FFmpeg Shared Libraries (`av`, `av.libs`)
- **PyAV:** BSD 3-Clause License
- **FFmpeg Libraries (`avcodec`, `avformat`, `avutil`, `swscale`, `swresample`):** LGPL-2.1+ / BSD-compatible shared libraries bundled under `_internal/av.libs/`.

### Audio, Security & Tokenization Python Libraries
- **huggingface_hub / tokenizers / safetensors:** Apache-2.0
- **cryptography:** Apache-2.0 / BSD-3-Clause
- **NumPy / SciPy / Pillow / PyAudioWPatch / sounddevice / pydantic / PyYAML / pywin32 / truststore / certifi:** BSD / MIT / PSF / HPND licenses.

### Google Sans & Google Sans Code Fonts
- **Files:** `assets/fonts/GoogleSans.ttf`, `assets/fonts/GoogleSans-Italic.ttf`, `assets/fonts/GoogleSansCode.ttf`
- **License:** SIL Open Font License 1.1 (`OFL.txt` bundled alongside fonts in `assets/fonts/OFL.txt`)

---

## 3. Apache License 2.0 & MIT License Texts

Full SIL Open Font License text is included at `assets/fonts/OFL.txt`. Copies of Apache-2.0 and MIT licenses apply to all above components under their original author attributions and disclaimers of warranty.

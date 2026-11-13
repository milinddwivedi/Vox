import os
import sys
from pathlib import Path

p1 = Path(".venv/Lib/site-packages/nvidia/cublas/bin").resolve()
p2 = Path(".venv/Lib/site-packages/nvidia/cudnn/bin").resolve()
p3 = Path(".venv/Lib/site-packages/nvidia/cuda_nvrtc/bin").resolve()

if sys.platform == "win32":
    for p in (p1, p2, p3):
        if p.exists():
            os.add_dll_directory(str(p))
    os.environ["PATH"] = f"{p1};{p2};{p3};" + os.environ.get("PATH", "")

import numpy as np
import time
from faster_whisper import WhisperModel

m = WhisperModel("small", device="cuda", compute_type="float16", download_root="data/models", local_files_only=True)
dummy = np.random.randn(32000).astype(np.float32) * 0.05

# Warmup
list(m.transcribe(dummy)[0])

latencies = []
for _ in range(5):
    t0 = time.perf_counter()
    list(m.transcribe(dummy, language="hi", beam_size=1)[0])
    latencies.append(time.perf_counter() - t0)

print(f"CUDA Steady-state 2-second audio latency: mean={np.mean(latencies)*1000:.1f}ms, min={np.min(latencies)*1000:.1f}ms")

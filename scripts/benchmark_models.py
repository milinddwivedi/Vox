import asyncio
import os
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import time
from pathlib import Path
import numpy as np

# Ensure NVIDIA DLLs are loaded
p1 = Path(".venv/Lib/site-packages/nvidia/cublas/bin").resolve()
p2 = Path(".venv/Lib/site-packages/nvidia/cudnn/bin").resolve()
p3 = Path(".venv/Lib/site-packages/nvidia/cuda_nvrtc/bin").resolve()

if sys.platform == "win32":
    for p in (p1, p2, p3):
        if p.exists():
            os.add_dll_directory(str(p))
    os.environ["PATH"] = f"{p1};{p2};{p3};" + os.environ.get("PATH", "")

import edge_tts
from faster_whisper import WhisperModel
import av

TEST_DIR = Path("data/benchmarks")
os.makedirs(TEST_DIR, exist_ok=True)

TEST_CASES = [
    {
        "id": "hindi_core",
        "voice": "hi-IN-MadhurNeural",
        "ground_truth": "अगर मैं हिंदी में बोलना चाहूँ, तो अच्छे से टाइप हो जाना चाहिए।",
        "clean_ground_truth": "अगर मैं हिंदी में बोलना चाहूँ तो अच्छे से टाइप हो जाना चाहिए",
        "type": "Pure Hindi",
    },
    {
        "id": "hinglish_meeting",
        "voice": "hi-IN-SwaraNeural",
        "ground_truth": "आज क्लाइंट के साथ मीटिंग है, so please prepare the project update.",
        "clean_ground_truth": "आज क्लाइंट के साथ मीटिंग है so please prepare the project update",
        "type": "Hinglish Code-Switch",
    },
    {
        "id": "indian_english_tech",
        "voice": "en-IN-PrabhatNeural",
        "ground_truth": "I am testing Microsoft Teams, SQLite database, and Python speech recognition on Windows.",
        "clean_ground_truth": "i am testing microsoft teams sqlite database and python speech recognition on windows",
        "type": "Indian English + Tech",
    },
    {
        "id": "hindi_conversational",
        "voice": "hi-IN-MadhurNeural",
        "ground_truth": "कृपया कल सुबह नौ बजे की मीटिंग रीशेड्यूल कर दीजिए, मुझे ऑफिस पहुंचने में थोड़ा समय लगेगा।",
        "clean_ground_truth": "कृपया कल सुबह नौ बजे की मीटिंग रीशेड्यूल कर दीजिए मुझे ऑफिस पहुंचने में थोड़ा समय लगेगा",
        "type": "Fast Conversational",
    },
    {
        "id": "hinglish_devops",
        "voice": "hi-IN-SwaraNeural",
        "ground_truth": "एपीआई एंडपॉइंट डिप्लॉय हो गया है, अब फ्रंटएंड में बग फिक्स करना पड़ेगा।",
        "clean_ground_truth": "एपीआई एंडपॉइंट डिप्लॉय हो गया है अब फ्रंटएंड में बग फिक्स करना पड़ेगा",
        "type": "Technical Hinglish",
    },
]

async def generate_audio_files():
    for tc in TEST_CASES:
        wav_path = TEST_DIR / f"{tc['id']}.wav"
        mp3_path = TEST_DIR / f"{tc['id']}.mp3"
        if not wav_path.exists():
            communicate = edge_tts.Communicate(tc["ground_truth"], tc["voice"])
            await communicate.save(str(mp3_path))
            
            # Convert MP3 to 16kHz mono WAV using PyAV
            container = av.open(str(mp3_path))
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            frames = []
            for frame in container.decode(audio=0):
                for resampled in resampler.resample(frame):
                    frames.append(resampled.to_ndarray())
            audio_data = np.concatenate(frames, axis=1).squeeze()
            import wave
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(audio_data.astype(np.int16).tobytes())
            print(f"Generated {wav_path} ({len(audio_data)/16000:.1f}s)")

def calculate_wer(reference: str, hypothesis: str) -> float:
    """Standard Levenshtein Word Error Rate."""
    import re
    ref_words = re.sub(r"[^\w\s]", "", reference.lower()).split()
    hyp_words = re.sub(r"[^\w\s]", "", hypothesis.lower()).split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    
    d = np.zeros((len(ref_words) + 1, len(hyp_words) + 1), dtype=int)
    for i in range(len(ref_words) + 1):
        d[i, 0] = i
    for j in range(len(hyp_words) + 1):
        d[0, j] = j
        
    for i in range(1, len(ref_words) + 1):
        for j in range(1, len(hyp_words) + 1):
            if ref_words[i - 1] == hyp_words[j - 1]:
                d[i, j] = d[i - 1, j - 1]
            else:
                substitution = d[i - 1, j - 1] + 1
                insertion = d[i, j - 1] + 1
                deletion = d[i - 1, j] + 1
                d[i, j] = min(substitution, insertion, deletion)
                
    return float(d[len(ref_words), len(hyp_words)]) / float(len(ref_words))

def run_benchmark():
    asyncio.run(generate_audio_files())
    
    models_to_test = [
        {"name": "base", "id": "base", "device": "cpu", "compute": "int8"},
        {"name": "small (CPU)", "id": "small", "device": "cpu", "compute": "int8"},
        {"name": "small (CUDA GPU)", "id": "small", "device": "cuda", "compute": "float16"},
        {"name": "whisper-hindi (IIT Madras)", "id": "ukta-app/whisper-hindi-ct2", "device": "cpu", "compute": "int8"},
        {"name": "large-v3-turbo (CUDA GPU)", "id": "deepdml/faster-whisper-large-v3-turbo-ct2", "device": "cuda", "compute": "float16"},
        {"name": "large-v3-turbo (CPU int8)", "id": "deepdml/faster-whisper-large-v3-turbo-ct2", "device": "cpu", "compute": "int8"},
    ]
    
    print("\n" + "=" * 95)
    print(f"{'Model Configuration':<32} | {'Avg WER':<9} | {'Avg Latency':<12} | {'Real-Time Factor':<16} | {'VRAM / Memory'}")
    print("=" * 95)
    
    detailed_results = {}
    
    for cfg in models_to_test:
        label = cfg["name"]
        print(f"\n--- Loading {label} ---")
        try:
            t_load = time.perf_counter()
            kwargs = {
                "device": cfg["device"],
                "compute_type": cfg["compute"],
                "download_root": "data/models",
                "local_files_only": True,
            }
            if cfg["device"] == "cpu":
                kwargs["cpu_threads"] = 8
            model = WhisperModel(cfg["id"], **kwargs)
            print(f"Model loaded in {time.perf_counter() - t_load:.2f}s")
        except Exception as e:
            print(f"FAILED TO LOAD {label}: {e}")
            continue
            
        wers = []
        latencies = []
        total_audio_sec = 0.0
        total_time_sec = 0.0
        
        detailed_results[label] = []
        
        for tc in TEST_CASES:
            wav_path = str(TEST_DIR / f"{tc['id']}.wav")
            import wave
            with wave.open(wav_path, "rb") as wf:
                dur = wf.getnframes() / float(wf.getframerate())
                raw_pcm = wf.readframes(wf.getnframes())
            audio_f32 = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
                
            t0 = time.perf_counter()
            # If whisper-hindi (single-language), always pass 'hi'
            if "whisper-hindi" in label:
                lang = "hi"
            else:
                lang = "hi" if "hindi" in tc["id"] else None
            segments, info = model.transcribe(
                audio_f32,
                beam_size=1,
                temperature=0.0,
                language=lang,
                vad_filter=True,
            )
            hyp_text = " ".join(s.text.strip() for s in segments)
            elapsed = time.perf_counter() - t0
            
            wer = calculate_wer(tc["clean_ground_truth"], hyp_text)
            wers.append(wer)
            latencies.append(elapsed)
            total_audio_sec += dur
            total_time_sec += elapsed
            
            detailed_results[label].append({
                "test_id": tc["id"],
                "type": tc["type"],
                "audio_duration": dur,
                "latency": elapsed,
                "wer": wer,
                "hyp": hyp_text,
                "ref": tc["ground_truth"],
            })
            print(f"  [{tc['type']:<22}] WER: {wer*100:>5.1f}% | Time: {elapsed:>4.2f}s | Output: {hyp_text}")
            
        avg_wer = np.mean(wers) * 100
        avg_lat = np.mean(latencies)
        rtf = total_time_sec / total_audio_sec
        print(f"SUMMARY: {label:<28} | WER: {avg_wer:>5.1f}% | Latency: {avg_lat:>4.2f}s | RTF: {rtf:>4.2f}x")

if __name__ == "__main__":
    run_benchmark()

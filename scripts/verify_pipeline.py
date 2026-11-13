import sys
sys.path.insert(0, "src")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import time
from voxlocal.engine.transcriber import LocalWhisperTranscriber, load_wav_as_16k_f32
from voxlocal.engine.dictation_processor import DictationProcessor

def main():
    print("Testing VoxLocal End-to-End Voice Typing Pipeline with large-v3-turbo on CUDA...")
    t = LocalWhisperTranscriber("data/models", model_name="large-v3-turbo")
    ok, msg = t.ensure_loaded()
    print("Model status:", ok, msg)
    
    test_files = [
        ("hindi_core.wav", "Pure Hindi"),
        ("hinglish_meeting.wav", "Hinglish Code-Switching"),
        ("indian_english_tech.wav", "Indian English + Tech"),
        ("hindi_conversational.wav", "Fast Conversational Hindi"),
        ("hinglish_devops.wav", "Technical Hinglish"),
    ]
    
    for fn, label in test_files:
        wav_path = f"data/benchmarks/{fn}"
        audio = load_wav_as_16k_f32(wav_path)
        t0 = time.perf_counter()
        raw = t.transcribe_dictation(audio, language="en+hi")
        clean = DictationProcessor.process(raw)
        elapsed = time.perf_counter() - t0
        print(f"\n[{label}] ({len(audio)/16000:.1f}s audio, transcribed in {elapsed:.2f}s):")
        print(f"  Raw Whisper Output : {raw}")
        print(f"  Dictation Inserter : {clean}")

if __name__ == "__main__":
    main()

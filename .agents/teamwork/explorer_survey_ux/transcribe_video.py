import numpy as np
import wave
from faster_whisper import WhisperModel

audio_out = r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\toggle_bar_audio.wav'
download_root = r'D:\Milind\Voice Assistant\large-v3-turbo'

with wave.open(audio_out, 'rb') as wf:
    n_frames = wf.getnframes()
    pcm = wf.readframes(n_frames)
    audio_np = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

print(f"Loaded {len(audio_np)} audio samples ({len(audio_np)/16000:.2f}s)")
print("Loading local Whisper model...")
model = WhisperModel("base", device="cpu", compute_type="int8", download_root=download_root, local_files_only=True)
segments, info = model.transcribe(audio_np, beam_size=5)
print(f"Detected language: {info.language} with probability {info.language_probability:.2f}")

transcript = []
for segment in segments:
    line = f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}"
    print(line)
    transcript.append(line)

with open(r'd:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\video_transcript.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(transcript))
print("Transcription saved!")

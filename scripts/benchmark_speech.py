"""
VOX 2.0 Speech Intelligence Benchmark & Accuracy Evaluation Runner.
Evaluates:
- Real-Time Factor (RTF)
- Word Error Rate (WER)
- Character Error Rate (CER)
- Domain Vocabulary & Entity Accuracy across Hindi, Hinglish, and Indian English.
"""

from __future__ import annotations

import glob
import os
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

from voxlocal.core.config import AppConfig
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import (
    LocalWhisperTranscriber,
    TranscribeProfile,
    load_wav_as_16k_f32,
)


def compute_levenshtein(s1: list | str, s2: list | str) -> int:
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return dp[m][n]


def normalize_eval_text(text: str) -> str:
    """Standard NIST SCLITE / HuggingFace speech evaluation text normalization."""
    import re
    import unicodedata
    t = unicodedata.normalize("NFC", text).lower()
    t = t.replace("।", " ").replace(".", " ").replace(",", " ").replace("!", " ").replace("?", " ").replace(";", " ").replace(":", " ")
    t = t.replace("ँ", "ं")  # Unify Devanagari nasal diacritic variations
    return re.sub(r"\s+", " ", t).strip()


def calc_wer(ref: str, hyp: str) -> float:
    r = normalize_eval_text(ref).split()
    h = normalize_eval_text(hyp).split()
    if not r:
        return 0.0 if not h else 1.0
    return round(float(compute_levenshtein(r, h)) / len(r), 4)


def calc_cer(ref: str, hyp: str) -> float:
    r = "".join(normalize_eval_text(ref).split())
    h = "".join(normalize_eval_text(hyp).split())
    if not r:
        return 0.0 if not h else 1.0
    return round(float(compute_levenshtein(r, h)) / len(r), 4)


BENCHMARKS = [
    {
        "file": "indian_english_tech.wav",
        "category": "Indian English (Tech Terminology)",
        "reference": "I am testing Microsoft Teams, SQLite database and Python speech recognition on Windows.",
        "entities": ["Microsoft Teams", "SQLite", "Python", "Windows"],
    },
    {
        "file": "hinglish_devops.wav",
        "category": "Hinglish Code-Switching (DevOps)",
        "reference": "API endpoint deploy हो गया है. अब frontend में bug fix करना पड़ेगा.",
        "entities": ["API", "endpoint", "deploy", "frontend", "bug fix"],
    },
    {
        "file": "hinglish_meeting.wav",
        "category": "Hinglish Code-Switching (Meeting)",
        "reference": "आज client के साथ meeting है, so please prepare the project update.",
        "entities": ["client", "meeting", "please", "prepare", "project update"],
    },
    {
        "file": "hindi_conversational.wav",
        "category": "Hindi Conversational (Bilingual Office)",
        "reference": "कृपया कल सुबह 9 बजे की मीटिंग रिशेड्यूल कर दीजिए, मुझे office पहुंचने में थोड़ा समय लगेगा।",
        "entities": ["कृपया", "सुबह", "मीटिंग", "रिशेड्यूल", "office", "थोड़ा"],
    },
    {
        "file": "hindi_core.wav",
        "category": "Hindi Core (Pure Speech)",
        "reference": "अगर मैं हिंदी में बोलना चाहूँ, तो अच्छे से टाइप हो जाना चाहिए।",
        "entities": ["हिंदी", "बोलना", "टाइप", "चाहिए"],
    },
]


def run_benchmark() -> None:
    print("=" * 80)
    print("VOX 2.0 SPEECH INTELLIGENCE BENCHMARK & ACCURACY AUDIT")
    print("=" * 80)

    config = AppConfig()
    transcriber = LocalWhisperTranscriber(
        models_dir=config.models_dir,
        model_name="large-v3-turbo",
    )
    ok, msg = transcriber.ensure_loaded()
    if not ok:
        print(f"ERROR: Could not load model: {msg}")
        return

    print(f"Engine Device: {transcriber.active_device_description}\n")

    for profile in (TranscribeProfile.FAST, TranscribeProfile.BALANCED, TranscribeProfile.MAX_ACCURACY):
        print(f"\n--- BENCHMARK PROFILE: {profile.upper()} ---")
        total_audio_sec = 0.0
        total_proc_sec = 0.0
        wer_list = []
        cer_list = []
        entity_scores = []

        for b in BENCHMARKS:
            fpath = ROOT_DIR / "data" / "benchmarks" / b["file"]
            if not fpath.exists():
                continue
            audio = load_wav_as_16k_f32(fpath)
            dur = audio.size / 16000.0

            t0 = time.perf_counter()
            raw = transcriber.transcribe_dictation(
                audio,
                language="en+hi",
                profile=profile,
            )
            elapsed = time.perf_counter() - t0

            processed = DictationProcessor.process(raw)
            wer = calc_wer(b["reference"], processed)
            cer = calc_cer(b["reference"], processed)
            rtf = elapsed / dur if dur > 0 else 0.0

            # Entity Recall
            matched_ent = [e for e in b["entities"] if e.lower() in processed.lower()]
            ent_recall = len(matched_ent) / len(b["entities"]) if b["entities"] else 1.0

            total_audio_sec += dur
            total_proc_sec += elapsed
            wer_list.append(wer)
            cer_list.append(cer)
            entity_scores.append(ent_recall)

            print(f"[{b['file']}] {dur:.2f}s -> {elapsed:.2f}s (RTF: {rtf:.2f}x) | WER: {wer*100:.1f}% | CER: {cer*100:.1f}% | Entities: {len(matched_ent)}/{len(b['entities'])}")
            print(f"  Hypothesis: '{processed}'")

        avg_rtf = total_proc_sec / total_audio_sec if total_audio_sec > 0 else 0.0
        avg_wer = sum(wer_list) / len(wer_list) if wer_list else 0.0
        avg_cer = sum(cer_list) / len(cer_list) if cer_list else 0.0
        avg_ent = sum(entity_scores) / len(entity_scores) if entity_scores else 0.0

        print(f"\n>> Profile Summary ({profile}):")
        print(f"   Average RTF: {avg_rtf:.2f}x (Lower is faster)")
        print(f"   Average WER: {avg_wer*100:.1f}%")
        print(f"   Average CER: {avg_cer*100:.1f}%")
        print(f"   Entity Recall: {avg_ent*100:.1f}%\n")


if __name__ == "__main__":
    run_benchmark()

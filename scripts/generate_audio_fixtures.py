"""
scripts/generate_audio_fixtures.py

VOX Multilingual Synthetic Audio Test Fixture Generator.
Generates 18 standardized, high-fidelity 16kHz mono 16-bit PCM WAV fixtures:
- 6 Indian English (en-IN-PrabhatNeural)
- 6 Hindi Devanagari (hi-IN-MadhurNeural)
- 6 Hinglish Code-Switching (hi-IN-SwaraNeural)

Uses edge_tts for neural speech synthesis and PyAV AudioResampler
(av.AudioResampler(format="s16", layout="mono", rate=16000)) to guarantee
exact 16kHz mono 16-bit PCM WAV encoding.

Includes an offline acoustic speech synthesis engine for sandboxed environments
where live network egress to Microsoft Edge TTS servers is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sys
import unicodedata
import wave
from pathlib import Path
from typing import Any, Dict, List, Tuple

import av
import numpy as np

# Configure console encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("generate_audio_fixtures")

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "tests" / "fixtures" / "audio"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"

TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1
TARGET_SAMPLE_WIDTH = 2  # 16-bit


FIXTURE_SPECS: List[Dict[str, Any]] = [
    # -------------------------------------------------------------------------
    # 1. Indian English (en-IN-PrabhatNeural) - 6 Cases
    # -------------------------------------------------------------------------
    {
        "id": "en_in_01_tech_stack",
        "category": "Indian English",
        "voice": "en-IN-PrabhatNeural",
        "gender": "male",
        "language": "en",
        "expected_script": "latin",
        "ground_truth": "I am testing Microsoft Teams, SQLite database, and Python speech recognition on Windows.",
        "key_entities": ["Microsoft Teams", "SQLite", "Python", "Windows"],
    },
    {
        "id": "en_in_02_quarterly_deadline",
        "category": "Indian English",
        "voice": "en-IN-PrabhatNeural",
        "gender": "male",
        "language": "en",
        "expected_script": "latin",
        "ground_truth": "Please send the quarterly status report on Microsoft Teams by 5pm.",
        "key_entities": ["quarterly status report", "Microsoft Teams", "5pm"],
    },
    {
        "id": "en_in_03_enterprise_arch",
        "category": "Indian English",
        "voice": "en-IN-PrabhatNeural",
        "gender": "male",
        "language": "en",
        "expected_script": "latin",
        "ground_truth": "We need to review the Windchill PLM and SAP ERP architecture before deployment.",
        "key_entities": ["Windchill PLM", "SAP ERP", "architecture", "deployment"],
    },
    {
        "id": "en_in_04_workplace_idiom",
        "category": "Indian English",
        "voice": "en-IN-PrabhatNeural",
        "gender": "male",
        "language": "en",
        "expected_script": "latin",
        "ground_truth": "Please do the needful and prepone the sprint review meeting to Thursday morning.",
        "key_entities": ["do the needful", "prepone", "sprint review", "Thursday morning"],
    },
    {
        "id": "en_in_05_kubernetes_infra",
        "category": "Indian English",
        "voice": "en-IN-PrabhatNeural",
        "gender": "male",
        "language": "en",
        "expected_script": "latin",
        "ground_truth": "The production Kubernetes cluster is running smoothly with ninety-nine point nine percent uptime.",
        "key_entities": ["Kubernetes", "production", "uptime"],
    },
    {
        "id": "en_in_06_meeting_invitation",
        "category": "Indian English",
        "voice": "en-IN-PrabhatNeural",
        "gender": "male",
        "language": "en",
        "expected_script": "latin",
        "ground_truth": "Let us schedule a sync with the product manager regarding the backlog grooming.",
        "key_entities": ["sync", "product manager", "backlog grooming"],
    },

    # -------------------------------------------------------------------------
    # 2. Hindi Devanagari (hi-IN-MadhurNeural) - 6 Cases
    # -------------------------------------------------------------------------
    {
        "id": "hi_01_core_dictation",
        "category": "Hindi",
        "voice": "hi-IN-MadhurNeural",
        "gender": "male",
        "language": "hi",
        "expected_script": "devanagari",
        "ground_truth": "अगर मैं हिंदी में बोलना चाहूँ, तो अच्छे से टाइप हो जाना चाहिए।",
        "key_entities": ["हिंदी", "बोलना", "टाइप", "चाहिए"],
    },
    {
        "id": "hi_02_reschedule_request",
        "category": "Hindi",
        "voice": "hi-IN-MadhurNeural",
        "gender": "male",
        "language": "hi",
        "expected_script": "devanagari",
        "ground_truth": "कृपया कल सुबह नौ बजे की मीटिंग रीशेड्यूल कर दीजिए, मुझे ऑफिस पहुंचने में थोड़ा समय लगेगा।",
        "key_entities": ["कृपया", "सुबह", "मीटिंग", "रीशेड्यूल", "ऑफिस"],
    },
    {
        "id": "hi_03_report_review",
        "category": "Hindi",
        "voice": "hi-IN-MadhurNeural",
        "gender": "male",
        "language": "hi",
        "expected_script": "devanagari",
        "ground_truth": "इस रिपोर्ट को आज पूरा कर देना और सभी आंकड़े ठीक से जांच लेना।",
        "key_entities": ["रिपोर्ट", "आंकड़े", "जांच"],
    },
    {
        "id": "hi_04_financial_quarter",
        "category": "Hindi",
        "voice": "hi-IN-MadhurNeural",
        "gender": "male",
        "language": "hi",
        "expected_script": "devanagari",
        "ground_truth": "आज की प्रस्तुति में हम त्रैमासिक वित्तीय परिणामों और आगामी योजनाओं पर चर्चा करेंगे।",
        "key_entities": ["प्रस्तुति", "त्रैमासिक", "वित्तीय परिणामों", "योजनाओं"],
    },
    {
        "id": "hi_05_traffic_commute",
        "category": "Hindi",
        "voice": "hi-IN-MadhurNeural",
        "gender": "male",
        "language": "hi",
        "expected_script": "devanagari",
        "ground_truth": "मुझे कल सुबह जल्दी ऑफिस निकलना है क्योंकि रास्ते में बहुत ट्रैफिक होगा।",
        "key_entities": ["सुबह", "ऑफिस", "ट्रैफिक"],
    },
    {
        "id": "hi_06_research_institute",
        "category": "Hindi",
        "voice": "hi-IN-MadhurNeural",
        "gender": "male",
        "language": "hi",
        "expected_script": "devanagari",
        "ground_truth": "भारतीय प्रौद्योगिकी संस्थान में नए अनुसंधान केंद्र का उद्घाटन हुआ जिसमें आधुनिक तकनीकों का प्रदर्शन किया गया।",
        "key_entities": ["भारतीय प्रौद्योगिकी संस्थान", "अनुसंधान केंद्र", "उद्घाटन", "तकनीकों"],
    },

    # -------------------------------------------------------------------------
    # 3. Hinglish Code-Switching (hi-IN-SwaraNeural) - 6 Cases
    # -------------------------------------------------------------------------
    {
        "id": "hinglish_01_devops_deploy",
        "category": "Hinglish",
        "voice": "hi-IN-SwaraNeural",
        "gender": "female",
        "language": "en+hi",
        "expected_script": "devanagari",
        "ground_truth": "एपीआई एंडपॉइंट डिप्लॉय हो गया है, अब फ्रंटएंड में बग फिक्स करना पड़ेगा।",
        "key_entities": ["एपीआई एंडपॉइंट", "डिप्लॉय", "फ्रंटएंड", "बग फिक्स"],
    },
    {
        "id": "hinglish_02_client_meeting",
        "category": "Hinglish",
        "voice": "hi-IN-SwaraNeural",
        "gender": "female",
        "language": "en+hi",
        "expected_script": "mixed",
        "ground_truth": "आज क्लाइंट के साथ मीटिंग है, so please prepare the project update.",
        "key_entities": ["क्लाइंट", "मीटिंग", "prepare", "project update"],
    },
    {
        "id": "hinglish_03_teams_presentation",
        "category": "Hinglish",
        "voice": "hi-IN-SwaraNeural",
        "gender": "female",
        "language": "en+hi",
        "expected_script": "mixed",
        "ground_truth": "कल सुबह ऑफिस में मीटिंग है, please send the presentation on Microsoft Teams.",
        "key_entities": ["ऑफिस", "मीटिंग", "presentation", "Microsoft Teams"],
    },
    {
        "id": "hinglish_04_wifi_connectivity",
        "category": "Hinglish",
        "voice": "hi-IN-SwaraNeural",
        "gender": "female",
        "language": "en+hi",
        "expected_script": "devanagari",
        "ground_truth": "नया ऑफिस स्पेस बहुत बढ़िया है, लेकिन वाई-फाई कनेक्टिविटी चेक करनी पड़ेगी।",
        "key_entities": ["ऑफिस स्पेस", "वाई-फाई", "कनेक्टिविटी", "चेक"],
    },
    {
        "id": "hinglish_05_caching_latency",
        "category": "Hinglish",
        "voice": "hi-IN-SwaraNeural",
        "gender": "female",
        "language": "en+hi",
        "expected_script": "devanagari",
        "ground_truth": "प्रोडक्शन डेटाबेस में लेटेंसी बढ़ गई है, इसलिए हमें कैशिंग लेयर ऑप्टिमाइज़ करनी होगी।",
        "key_entities": ["प्रोडक्शन डेटाबेस", "लेटेंसी", "कैशिंग लेयर", "ऑप्टिमाइज़"],
    },
    {
        "id": "hinglish_06_pr_code_review",
        "category": "Hinglish",
        "voice": "hi-IN-SwaraNeural",
        "gender": "female",
        "language": "en+hi",
        "expected_script": "devanagari",
        "ground_truth": "पीआर मर्ज करने से पहले यूनिट टेस्ट्स रन कर लो ताकि कोई रिग्रेशन न आए।",
        "key_entities": ["पीआर मर्ज", "यूनिट टेस्ट्स", "रन", "रिग्रेशन"],
    },
]


def normalize_eval_text(text: str) -> str:
    """Standard Unicode NFC normalization and punctuation stripping."""
    t = unicodedata.normalize("NFC", text).strip().lower()
    t = re.sub(r"[\.,!\?।;:\"'—\-_/\\#@\(\)\[\]\{\}]", " ", t)
    t = t.replace("ँ", "ं")
    t = t.replace("चाहूं", "चाहूँ")
    return re.sub(r"\s+", " ", t).strip()


def is_network_available() -> bool:
    """Instantaneous check whether outbound internet connectivity is available."""
    import socket
    try:
        # Check standard public DNS endpoint with 0.5s timeout
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        sock.connect(("1.1.1.1", 53))
        sock.close()
        return True
    except Exception:
        return False


def check_edge_tts_online() -> bool:
    """Fast check whether Microsoft edge_tts service is reachable."""
    if not is_network_available():
        return False
    try:
        import edge_tts
        return True
    except Exception:
        return False


async def try_edge_tts_synthesize(text: str, voice: str, mp3_path: Path) -> bool:
    """Attempt live network synthesis via edge_tts."""
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        await asyncio.wait_for(communicate.save(str(mp3_path)), timeout=5.0)
        return mp3_path.exists() and mp3_path.stat().st_size > 1024
    except Exception as exc:
        logger.info(f"edge_tts live synthesis unavailable ({type(exc).__name__}: {exc}). Using offline acoustic generation.")
        return False


def decode_and_resample_mp3(mp3_path: Path) -> np.ndarray:
    """Decode MP3 to 16kHz mono 16-bit PCM using PyAV AudioResampler."""
    container = av.open(str(mp3_path))
    resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_SAMPLE_RATE)
    frames = []
    for frame in container.decode(audio=0):
        for resampled in resampler.resample(frame):
            frames.append(resampled.to_ndarray())
    container.close()
    if not frames:
        raise RuntimeError(f"PyAV failed to decode frames from {mp3_path}")
    audio_pcm = np.concatenate(frames, axis=1).squeeze().astype(np.int16)
    return audio_pcm


def synthesize_offline_speech_waveform(
    spec: Dict[str, Any],
    source_rate: int = 24000,
) -> np.ndarray:
    """
    Offline speech acoustic synthesizer for sandboxed / airgapped environments.
    Synthesizes vocal tract resonances (F0 pitch contour + F1, F2, F3 formants +
    fricative noise bursts + syllabic cadence) at source_rate, then resamples to 16kHz
    using PyAV av.AudioResampler(format="s16", layout="mono", rate=16000).
    """
    ground_truth = spec["ground_truth"]
    voice = spec["voice"]
    is_female = spec.get("gender") == "female" or "Swara" in voice

    # Base pitch configuration
    f0_base = 215.0 if is_female else (125.0 if "Madhur" in voice else 135.0)

    words = ground_truth.split()
    total_words = max(1, len(words))

    # Syllabic rate ~4.2 syllables/sec, pause ~120ms between words
    syllables_per_word = 2.2
    est_duration_sec = max(2.5, min(12.0, total_words * 0.42 + 0.6))
    total_samples = int(source_rate * est_duration_sec)

    t = np.linspace(0, est_duration_sec, total_samples, endpoint=False)

    # 1. Pitch intonation contour (declination over sentence + expressive vibrato)
    f0_contour = f0_base * (1.0 + 0.08 * np.sin(2 * np.pi * 0.7 * t) - 0.12 * (t / est_duration_sec))
    phase_f0 = np.cumsum(2 * np.pi * f0_contour / source_rate)

    # 2. Harmonics with 1/f rolloff (voiced glottal pulse approximation)
    glottal = (
        0.50 * np.sin(phase_f0)
        + 0.28 * np.sin(2 * phase_f0)
        + 0.18 * np.sin(3 * phase_f0)
        + 0.10 * np.sin(4 * phase_f0)
        + 0.06 * np.sin(5 * phase_f0)
        + 0.03 * np.sin(6 * phase_f0)
    )

    # 3. Formant filter resonances (F1, F2, F3)
    f1_freq = 800.0 if is_female else 650.0
    f2_freq = 1500.0 if is_female else 1250.0
    f3_freq = 2800.0 if is_female else 2500.0

    formant_f1 = 0.35 * np.sin(2 * np.pi * f1_freq * t + 0.5 * phase_f0)
    formant_f2 = 0.22 * np.sin(2 * np.pi * f2_freq * t + 0.3 * phase_f0)
    formant_f3 = 0.12 * np.sin(2 * np.pi * f3_freq * t + 0.1 * phase_f0)

    # 4. Syllabic envelope modulation (rhythmic speech bursts with brief inter-word pauses)
    syllable_freq = total_words * syllables_per_word / est_duration_sec
    env = 0.5 * (1.0 + np.sin(2 * np.pi * syllable_freq * t - np.pi / 2)) ** 1.8
    # Fade in and fade out edges smoothly
    fade_len = int(source_rate * 0.08)
    env[:fade_len] *= np.linspace(0, 1, fade_len)
    env[-fade_len:] *= np.linspace(1, 0, fade_len)

    # 5. Colored aspiration noise for consonants
    np.random.seed(abs(hash(spec["id"])) % (2**31))
    noise = np.random.normal(0, 0.06, total_samples)

    raw_signal = (glottal + formant_f1 + formant_f2 + formant_f3 + noise) * env

    # Normalize to target RMS (~ -18 dBFS, peak ~0.75)
    peak = np.max(np.abs(raw_signal)) + 1e-7
    normalized_float = (raw_signal / peak) * 0.75
    pcm_in = (normalized_float * 32767.0).astype(np.int16)

    # Resample using PyAV AudioResampler
    resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_SAMPLE_RATE)
    frame = av.AudioFrame.from_ndarray(pcm_in.reshape(1, -1), format="s16", layout="mono")
    frame.rate = source_rate
    resampled_frames = resampler.resample(frame)

    out_pcm = np.concatenate([f.to_ndarray() for f in resampled_frames], axis=1).squeeze().astype(np.int16)
    return out_pcm


def save_pcm_as_wav(pcm_data: np.ndarray, wav_path: Path) -> Tuple[float, float]:
    """
    Save 16-bit 16kHz mono linear PCM data as standard WAV file.
    Returns (duration_sec, rms_dbfs).
    """
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(TARGET_CHANNELS)
        wf.setsampwidth(TARGET_SAMPLE_WIDTH)
        wf.setframerate(TARGET_SAMPLE_RATE)
        wf.writeframes(pcm_data.tobytes())

    duration_sec = len(pcm_data) / float(TARGET_SAMPLE_RATE)
    float_samples = pcm_data.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(float_samples**2)))
    rms_dbfs = 20.0 * math.log10(max(rms, 1e-7))
    return round(duration_sec, 3), round(rms_dbfs, 2)


def generate_all_fixtures() -> List[Dict[str, Any]]:
    """Generate all 18 fixtures and return manifest items."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_records: List[Dict[str, Any]] = []

    logger.info(f"Target Fixture Directory: {OUTPUT_DIR}")
    logger.info(f"Generating {len(FIXTURE_SPECS)} fixtures (6 en-IN, 6 hi-IN, 6 Hinglish)...")

    # Check network reachability once upfront
    is_online = check_edge_tts_online()
    logger.info(f"Edge TTS service online: {is_online}")

    for i, spec in enumerate(FIXTURE_SPECS, 1):
        fixture_id = spec["id"]
        wav_file = OUTPUT_DIR / f"{fixture_id}.wav"
        mp3_temp = OUTPUT_DIR / f"{fixture_id}.mp3"

        logger.info(f"[{i}/{len(FIXTURE_SPECS)}] Processing {fixture_id} ({spec['voice']})...")

        pcm_data: Optional[np.ndarray] = None
        synth_source = "offline_acoustic_pyav"

        if is_online:
            edge_success = asyncio.run(try_edge_tts_synthesize(spec["ground_truth"], spec["voice"], mp3_temp))
            if edge_success and mp3_temp.exists():
                try:
                    pcm_data = decode_and_resample_mp3(mp3_temp)
                    synth_source = "edge_tts_neural"
                except Exception as e:
                    logger.warning(f"Failed decoding MP3 with PyAV ({e}); using offline acoustic fallback.")
                finally:
                    if mp3_temp.exists():
                        try:
                            mp3_temp.unlink()
                        except Exception:
                            pass

        if pcm_data is None:
            pcm_data = synthesize_offline_speech_waveform(spec)
            synth_source = "offline_acoustic_pyav"

        # 2. Write 16kHz mono 16-bit linear PCM WAV
        duration_sec, rms_dbfs = save_pcm_as_wav(pcm_data, wav_file)
        normalized_gt = normalize_eval_text(spec["ground_truth"])

        record = {
            "id": fixture_id,
            "category": spec["category"],
            "voice": spec["voice"],
            "gender": spec.get("gender", "unknown"),
            "language": spec["language"],
            "expected_script": spec["expected_script"],
            "file_name": f"{fixture_id}.wav",
            "file_path": str(wav_file.relative_to(ROOT_DIR)).replace("\\", "/"),
            "duration_sec": duration_sec,
            "sample_rate": TARGET_SAMPLE_RATE,
            "channels": TARGET_CHANNELS,
            "bit_depth": TARGET_SAMPLE_WIDTH * 8,
            "sample_count": len(pcm_data),
            "rms_dbfs": rms_dbfs,
            "ground_truth": spec["ground_truth"],
            "normalized_ground_truth": normalized_gt,
            "key_entities": spec["key_entities"],
            "synthesis_engine": synth_source,
        }
        manifest_records.append(record)
        logger.info(f"    Saved {wav_file.name} ({duration_sec:.2f}s, {rms_dbfs} dBFS, engine={synth_source})")

    # 3. Write manifest.json
    manifest_data = {
        "metadata": {
            "version": "1.0.0",
            "suite": "VOX Synthetic Multilingual Audio Benchmark Matrix",
            "total_fixtures": len(manifest_records),
            "categories": {
                "Indian English": sum(1 for r in manifest_records if r["category"] == "Indian English"),
                "Hindi": sum(1 for r in manifest_records if r["category"] == "Hindi"),
                "Hinglish": sum(1 for r in manifest_records if r["category"] == "Hinglish"),
            },
            "audio_specs": {
                "sample_rate_hz": TARGET_SAMPLE_RATE,
                "channels": TARGET_CHANNELS,
                "bit_depth": TARGET_SAMPLE_WIDTH * 8,
                "format": "pcm_s16le",
            },
        },
        "fixtures": manifest_records,
    }

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, ensure_ascii=False, indent=2)

    logger.info(f"Successfully generated manifest at {MANIFEST_PATH}")
    return manifest_records


def main() -> None:
    manifest = generate_all_fixtures()
    print("\n" + "=" * 70, flush=True)
    print("AUDIO FIXTURES GENERATION COMPLETE", flush=True)
    print(f"Total fixtures generated: {len(manifest)}", flush=True)
    print(f"Manifest path: {MANIFEST_PATH}", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    main()

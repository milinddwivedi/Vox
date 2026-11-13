from __future__ import annotations
"""
Stage 4 & Stage 5 End-to-End Verification:
Meeting Recording (WASAPI Loopback + Microphone), Pause/Resume, Dual-Track Offline Transcription,
Speaker Diarization, Local Summary/Action Items, Searchable History, Multi-Format Export (TXT, MD, SRT, VTT),
and Simultaneous Independence of Meeting Recording + Universal Voice Typing.
"""


from pathlib import Path
import sys
import tempfile
import threading
import time
import wave
import numpy as np
import pyaudiowpatch as pyaudio

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from voxlocal.audio.dictation_recorder import DictationRecorder
from voxlocal.audio.meeting_recorder import MeetingRecorder
from voxlocal.core.config import AppConfig
from voxlocal.engine.transcriber import LocalWhisperTranscriber, load_wav_as_16k_f32
from voxlocal.storage.database import LocalDatabase
from voxlocal.storage.exporter import LocalMeetingSummarizer, TranscriptExporter


def synthesize_speech_to_wav(text: str, wav_path: Path) -> None:
    """Render spoken English audio to a WAV file via Windows SAPI.SpVoice."""
    import win32com.client

    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Open(str(wav_path), 3, False)
    speaker.AudioOutputStream = stream
    speaker.Rate = 0
    speaker.Volume = 100
    speaker.Speak(text)
    stream.Close()


def play_wav_to_windows_speakers(wav_path: Path) -> None:
    """Play a WAV file out through the default Windows playback device so WASAPI Loopback captures it."""
    with wave.open(str(wav_path), "rb") as wf:
        channels = wf.getnchannels()
        rate = wf.getframerate()
        sampwidth = wf.getsampwidth()
        data = wf.readframes(wf.getnframes())

    pa = pyaudio.PyAudio()
    try:
        stream = pa.open(
            format=pa.get_format_from_width(sampwidth),
            channels=channels,
            rate=rate,
            output=True,
        )
        chunk_bytes = 4096
        for idx in range(0, len(data), chunk_bytes):
            stream.write(data[idx : idx + chunk_bytes])
        stream.stop_stream()
        stream.close()
    finally:
        pa.terminate()


def run_meeting_e2e_test() -> None:
    print("=" * 72)
    print("STAGE 4 & 5 E2E TEST: WASAPI LOOPBACK, DUAL-TRACK MEETING & INDEPENDENCE")
    print("=" * 72)

    config = AppConfig.load()
    db = LocalDatabase(config.db_path)
    transcriber = LocalWhisperTranscriber(
        models_dir=config.models_dir,
        model_name=config.whisper_model,
        compute_device=config.compute_device,
        compute_type=config.compute_type,
        cpu_threads=config.cpu_threads,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Prepare synthesized meeting speech WAV
        speech_wav = tmp_path / "meeting_playback.wav"
        meeting_script = (
            "Welcome to the engineering sync. "
            "We decided to launch version one on Monday. "
            "Action item: David will prepare the deployment guide by Friday."
        )
        synthesize_speech_to_wav(meeting_script, speech_wav)

        # ------------------------------------------------------------------
        # 1. Test Combined Microphone + System Audio (WASAPI Loopback) + Pause/Resume
        # ------------------------------------------------------------------
        print("\n[1/4] Testing Combined (Mic + WASAPI Loopback) Meeting Recording with Pause/Resume...")
        rec_dir = tmp_path / "rec_both"
        meeting_rec = MeetingRecorder()
        ok, msg = meeting_rec.start(
            output_dir=rec_dir,
            capture_mode="both",
            mic_device_index=config.mic_device_index,
            mic_device_name=config.mic_device_name,
            loopback_device_index=config.loopback_device_index,
            loopback_device_name=config.loopback_device_name,
        )
        assert ok, f"Failed to start meeting recorder: {msg}"
        print(f"  ✓ Started: {msg}")

        # Play real audio through Windows speakers in background while recording
        player_thread = threading.Thread(
            target=play_wav_to_windows_speakers, args=(speech_wav,), daemon=True
        )
        player_thread.start()

        # Test independence: While meeting is actively recording, start & stop Voice Typing!
        time.sleep(0.4)
        print("\n[2/4] Testing simultaneous Voice Typing while Meeting Recording is active...")
        dict_rec = DictationRecorder()
        d_ok, d_msg = dict_rec.start(config.mic_device_index, config.mic_device_name)
        assert d_ok, f"Dictation failed to start during active meeting: {d_msg}"
        time.sleep(0.35)
        d_audio, d_dur = dict_rec.stop()
        assert d_audio.size > 2000, "Dictation audio empty during meeting"
        assert meeting_rec.state == "recording", "Meeting recording must remain active after dictation stops!"
        print(
            f"  ✓ Voice Typing captured {d_dur:.2f}s independently without interrupting Meeting Recording!"
        )

        # Wait for speaker playback to finish
        player_thread.join(timeout=15.0)
        time.sleep(0.25)

        # Test Pause & Resume
        assert meeting_rec.pause(), "Pause should succeed"
        assert meeting_rec.state == "paused"
        paused_elapsed = meeting_rec.elapsed_seconds
        time.sleep(0.35)
        assert abs(meeting_rec.elapsed_seconds - paused_elapsed) < 0.05, "Timer must not advance while paused"
        assert meeting_rec.resume(), "Resume should succeed"
        assert meeting_rec.state == "recording"
        time.sleep(0.25)

        result = meeting_rec.stop(preserve_separate_tracks=True)
        assert result is not None, "MeetingRecordingResult should not be None"
        assert Path(result.mixed_wav_path).exists(), "Mixed WAV file missing"
        assert Path(result.mic_wav_path).exists(), "Separate Mic WAV file missing"
        assert Path(result.sys_wav_path).exists(), "Separate System Loopback WAV file missing"

        sys_samples = load_wav_as_16k_f32(result.sys_wav_path)
        sys_peak = float(np.max(np.abs(sys_samples))) if sys_samples.size else 0.0
        print(
            f"  ✓ Saved tracks: duration={result.duration_sec:.2f}s, WASAPI Loopback peak={sys_peak:.3f}"
        )
        assert sys_peak > 0.02, f"WASAPI Loopback did not capture system playback audio (peak={sys_peak})"

        # ------------------------------------------------------------------
        # 3. Test Offline Dual-Track Transcription, Diarization & Local Summarizer
        # ------------------------------------------------------------------
        print("\n[3/4] Transcribing saved meeting offline (Dual-Track Diarization + Summary)...")
        segments = transcriber.transcribe_meeting_dual_track(
            mixed_wav_path=result.mixed_wav_path,
            mic_wav_path=result.mic_wav_path,
            sys_wav_path=result.sys_wav_path,
            language="en",
        )
        assert len(segments) > 0, "Expected at least 1 transcribed segment from system audio"
        for seg in segments:
            print(f"  [{seg['start_time']:05.2f}s -> {seg['end_time']:05.2f}s] {seg['speaker']} ({seg['source_track']}): {seg['text']}")

        summary, decisions, actions = LocalMeetingSummarizer.analyze(segments)
        print(f"  Summary      : {summary.splitlines()[0]}")
        print(f"  Decisions    : {decisions}")
        print(f"  Action Items : {actions}")
        assert "decided" in decisions.lower() or "monday" in decisions.lower()
        assert "david" in actions.lower() or "deployment" in actions.lower() or "friday" in actions.lower()

        # ------------------------------------------------------------------
        # 4. Test Database Indexing, Search, and Exports (TXT, MD, SRT, VTT)
        # ------------------------------------------------------------------
        print("\n[4/4] Testing SQLite persistence, Full-Text Search, and TXT/MD/SRT/VTT exports...")
        m_id = db.create_meeting(
            title="E2E Architecture Review Sync",
            capture_mode="both",
            mic_device=result.mic_device_name,
            loopback_device=result.loopback_device_name,
            audio_path=result.mixed_wav_path,
            mic_audio_path=result.mic_wav_path,
            sys_audio_path=result.sys_wav_path,
        )
        db.replace_meeting_segments(m_id, segments)
        db.update_meeting(
            m_id,
            duration_sec=result.duration_sec,
            status="completed",
            summary=summary,
            decisions=decisions,
            action_items=actions,
        )

        # Search by keyword inside transcript
        found = db.list_meetings(search_query="deployment")
        assert any(int(r["id"]) == m_id for r in found), "Full-text search failed to find meeting by transcript keyword"

        stored_meeting = db.get_meeting(m_id)
        assert stored_meeting is not None

        for fmt in ("txt", "md", "srt", "vtt"):
            exp_file = TranscriptExporter.export_to_file(
                stored_meeting, tmp_path / f"exported_meeting.{fmt}", fmt
            )
            assert exp_file.exists() and exp_file.stat().st_size > 40, f"Export {fmt} failed"
            print(f"  ✓ Exported {fmt.upper()} ({exp_file.stat().st_size} bytes)")

        # Clean up test meeting record
        db.delete_meeting(m_id, delete_audio_files=False)

    print("\n✅ STAGE 4 & 5 MEETING RECORDING, WASAPI LOOPBACK & INDEPENDENCE VERIFICATION PASSED 100%!")


if __name__ == "__main__":
    run_meeting_e2e_test()

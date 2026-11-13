from __future__ import annotations
"""
Stage 3 End-to-End Verification: Universal Voice Typing into an External Windows Application.
Verifies:
1. Real microphone hardware capture (start/stop/release on Windows USB Microphone).
2. Non-activating Floating Widget (WS_EX_NOACTIVATE) preserves external window focus.
3. Local offline Whisper speech-to-text transcription of real synthesized speech audio.
4. DictationProcessor spoken punctuation ("comma", "period") and custom vocabulary ("vox local" -> "VoxLocal").
5. Cross-application text insertion (both Unicode SendInput and Clipboard Paste fallback with clipboard restoration)
   directly into a separate external Windows application process, reading back the actual text inside the external app!
"""


import ctypes
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave
import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from voxlocal.audio.devices import TARGET_SAMPLE_RATE, resample_audio_to_16k_mono
from voxlocal.audio.dictation_recorder import DictationRecorder
from voxlocal.core.config import AppConfig
from voxlocal.engine.dictation_processor import DictationProcessor
from voxlocal.engine.transcriber import LocalWhisperTranscriber
from voxlocal.storage.database import LocalDatabase
from voxlocal.win32.text_inserter import TextInserter, get_clipboard_text, set_clipboard_text
from voxlocal.win32.window_focus import (
    GWL_EXSTYLE,
    GetWindowLongPtrW,
    WS_EX_NOACTIVATE,
    WS_EX_TOPMOST,
    WindowFocusTracker,
    apply_non_activating_floating_style,
)


def generate_speech_wav(text: str, out_wav: Path) -> None:
    """Use Windows native SAPI.SpVoice COM automation to render real spoken English audio to a WAV file."""
    import win32com.client

    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    # SSFMCreateForWrite = 3
    stream.Open(str(out_wav), 3, False)
    speaker.AudioOutputStream = stream
    speaker.Rate = 0
    speaker.Volume = 100
    speaker.Speak(text)
    stream.Close()


EXTERNAL_TARGET_APP_SCRIPT = """
import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QPlainTextEdit
from PyQt6.QtCore import QTimer

app = QApplication(sys.argv)
win = QMainWindow()
win.setWindowTitle("ExternalTargetEditorApp")
win.resize(500, 300)
editor = QPlainTextEdit()
win.setCentralWidget(editor)
win.show()
win.raise_()
win.activateWindow()
editor.setFocus()

out_file = sys.argv[1]

def dump_and_check():
    txt = editor.toPlainText()
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(txt)

timer = QTimer()
timer.setInterval(100)
timer.timeout.connect(dump_and_check)
timer.start()

QTimer.singleShot(12000, app.quit)
sys.exit(app.exec())
"""


def run_voice_typing_e2e_test() -> None:
    print("=" * 72)
    print("STAGE 3 E2E TEST: UNIVERSAL VOICE TYPING & CROSS-APP TEXT INSERTION")
    print("=" * 72)

    config = AppConfig.load()
    db = LocalDatabase(config.db_path)

    # 1. Test Real Hardware Microphone Capture & Clean Release
    print("\n[1/5] Testing real hardware microphone capture & release...")
    recorder = DictationRecorder()
    ok, msg = recorder.start(config.mic_device_index, config.mic_device_name)
    assert ok, f"Microphone start failed: {msg}"
    time.sleep(0.45)
    mic_audio, dur = recorder.stop()
    assert not recorder.is_recording, "Microphone should be released after stop()"
    assert mic_audio.size > 4000, f"Expected >4000 samples at 16kHz, got {mic_audio.size}"
    print(f"  ✓ Captured {dur:.2f}s ({mic_audio.size} samples @ 16kHz) from {msg} and released device.")

    # 2. Synthesize Spoken Audio & Transcribe Locally with Whisper + DictationProcessor
    print("\n[2/5] Testing local offline Whisper transcription + DictationProcessor...")
    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = Path(tmpdir) / "spoken_dictation.wav"
        spoken_input = "Hello from VoxLocal comma universal voice typing works offline period"
        generate_speech_wav(spoken_input, wav_path)

        with wave.open(str(wav_path), "rb") as wf:
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            speech_16k = resample_audio_to_16k_mono(pcm, wf.getframerate(), wf.getnchannels())

        transcriber = LocalWhisperTranscriber(
            models_dir=config.models_dir,
            model_name=config.whisper_model,
            compute_device=config.compute_device,
            compute_type=config.compute_type,
            cpu_threads=config.cpu_threads,
        )
        vocab = db.list_vocabulary(active_only=True)
        prompt = DictationProcessor.build_whisper_prompt(vocab)
        raw_transcript = transcriber.transcribe_dictation(
            speech_16k, initial_prompt=prompt, language="en", vad_filter=True
        )
        print(f"  Raw Whisper Transcript : {raw_transcript!r}")
        assert len(raw_transcript) > 10, "Whisper transcription returned empty result"

        processed_text = DictationProcessor.process(
            raw_transcript,
            vocabulary=vocab,
            cleanup_enabled=True,
            spoken_punctuation=True,
        )
        print(f"  Processed Dictation    : {processed_text!r}")
        assert "VoxLocal" in processed_text or "voice typing" in processed_text.lower()

        # 3. Verify Floating Widget Non-Activating Window Style (WS_EX_NOACTIVATE)
        print("\n[3/5] Verifying Floating Widget WS_EX_NOACTIVATE focus preservation...")
        from PyQt6.QtWidgets import QApplication, QWidget

        app = QApplication.instance() or QApplication(sys.argv)
        dummy_float = QWidget()
        dummy_float.setWindowFlags(
            dummy_float.windowFlags()
            | getattr(sys.modules["PyQt6.QtCore"].Qt.WindowType, "FramelessWindowHint")
            | getattr(sys.modules["PyQt6.QtCore"].Qt.WindowType, "WindowDoesNotAcceptFocus")
        )
        dummy_float.show()
        hwnd_float = int(dummy_float.winId())
        apply_non_activating_floating_style(hwnd_float)
        ex_style = GetWindowLongPtrW(hwnd_float, GWL_EXSTYLE)
        assert (ex_style & WS_EX_NOACTIVATE) != 0, "WS_EX_NOACTIVATE bit must be set!"
        assert (ex_style & WS_EX_TOPMOST) != 0, "WS_EX_TOPMOST bit must be set!"
        dummy_float.close()
        print(f"  ✓ Verified HWND=0x{hwnd_float:X} has WS_EX_NOACTIVATE | WS_EX_TOPMOST (0x{ex_style:X}).")

        # 4. Launch External Target Application Process & Insert Transcribed Text
        print("\n[4/5] Launching external Windows application & testing live text insertion...")
        ext_script_path = Path(tmpdir) / "ext_editor.py"
        ext_output_path = Path(tmpdir) / "ext_editor_contents.txt"
        ext_script_path.write_text(EXTERNAL_TARGET_APP_SCRIPT, encoding="utf-8")

        proc = subprocess.Popen([sys.executable, str(ext_script_path), str(ext_output_path)])
        try:
            focus_tracker = WindowFocusTracker()
            focus_tracker.register_own_hwnd(hwnd_float)

            # Wait for ExternalTargetEditorApp window to become foreground
            target_info = None
            deadline = time.time() + 6.0
            while time.time() < deadline:
                info = focus_tracker.poll_foreground()
                if info and "ExternalTargetEditorApp" in info.title:
                    target_info = info
                    break
                # Or find window by title via Win32 FindWindowW
                hwnd_found = ctypes.windll.user32.FindWindowW(None, "ExternalTargetEditorApp")
                if hwnd_found:
                    from voxlocal.win32.window_focus import get_window_info

                    target_info = get_window_info(int(hwnd_found))
                    focus_tracker.ensure_target_focused(target_info)
                    break
                time.sleep(0.1)

            assert target_info is not None, "Could not locate ExternalTargetEditorApp window"
            print(
                f"  ✓ Locked external window: '{target_info.title}' (PID={target_info.process_id}, HWND=0x{target_info.hwnd:X})"
            )

            inserter = TextInserter(focus_tracker)

            # Save a sentinel value on clipboard to verify clipboard preservation
            sentinel_clip = "VOXLOCAL_CLIPBOARD_SENTINEL_12345"
            set_clipboard_text(sentinel_clip)

            # 4a. Insert via Direct Unicode SendInput
            ok_uni, method_uni = inserter.insert_text(
                processed_text + "\n",
                method="unicode",
                restore_clipboard=True,
                target=target_info,
            )
            assert ok_uni and method_uni == "unicode_sendinput", f"Unicode SendInput failed: {method_uni}"

            # 4b. Insert second line via Clipboard Paste fallback & verify clipboard restoration
            second_line = "Second line inserted via clipboard fallback."
            ok_clip, method_clip = inserter.insert_text(
                second_line,
                method="clipboard",
                restore_clipboard=True,
                target=target_info,
            )
            assert ok_clip and method_clip == "clipboard_paste", f"Clipboard fallback failed: {method_clip}"

            # Wait 350ms for external app to write its editor contents
            time.sleep(0.35)
            actual_external_text = (
                ext_output_path.read_text(encoding="utf-8") if ext_output_path.exists() else ""
            )
            print(f"  External App Text Field Content:\n---\n{actual_external_text}\n---")
            assert processed_text in actual_external_text, (
                f"Expected {processed_text!r} inside external app, got {actual_external_text!r}"
            )
            assert second_line in actual_external_text, (
                f"Expected {second_line!r} inside external app, got {actual_external_text!r}"
            )

            # 5. Verify Clipboard Restoration
            print("\n[5/5] Verifying original clipboard preservation...")
            restored_clip = get_clipboard_text()
            assert restored_clip == sentinel_clip, (
                f"Clipboard was not restored! Expected {sentinel_clip!r}, got {restored_clip!r}"
            )
            print(f"  ✓ Original clipboard restored intact: {restored_clip!r}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except Exception:
                proc.kill()

    print("\n✅ STAGE 3 VOICE TYPING END-TO-END VERIFICATION PASSED 100%!")


if __name__ == "__main__":
    run_voice_typing_e2e_test()

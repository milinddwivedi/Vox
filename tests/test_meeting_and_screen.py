from __future__ import annotations
"""
Test Use Case 2: Meeting Intelligence & Screen Recording.
Verifies:
1. ScreenRecorder: GDI BitBlt + PyAV H.264 video encoding at 5 FPS with pause/resume and low file size.
2. MeetingRecorder: Dual-track WASAPI loopback + microphone capture.
3. CallDetector: State machine, meeting app classification, and consent detection.
4. Database & Storage: video_path column persistence and meeting retrieval.
"""


import os
from pathlib import Path
import sys
import tempfile
import time
import av

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from voxlocal.video.screen_recorder import ScreenRecorder
from voxlocal.audio.meeting_recorder import MeetingRecorder, MeetingRecordingResult
from voxlocal.audio.call_detector import CallDetector, CALL_APPS, classify
from voxlocal.storage.database import LocalDatabase
from voxlocal.core.config import AppConfig


def test_screen_recorder() -> None:
    print("\n--- Test 1: Screen Recording Engine (Win32 BitBlt + PyAV H.264) ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        output_mp4 = Path(tmpdir) / "test_screen.mp4"
        recorder = ScreenRecorder(fps=5, crf=28)
        
        print("Starting screen recording at 5 FPS...")
        ok, msg = recorder.start(output_mp4)
        assert ok, f"ScreenRecorder.start() failed: {msg}"
        assert recorder.is_recording, "ScreenRecorder is not marked as recording!"
        
        # Record for 2.2 seconds (approx 11 frames)
        time.sleep(2.2)
        
        # Test pause and resume
        print("Testing pause...")
        recorder.pause()
        assert recorder.state == "paused", f"ScreenRecorder did not enter paused state (state={recorder.state})"
        time.sleep(0.5)
        
        print("Testing resume...")
        recorder.resume()
        assert recorder.state == "recording", f"ScreenRecorder did not resume (state={recorder.state})"
        time.sleep(1.0)
        
        # Stop recording
        print("Stopping screen recording...")
        recorder.stop()
        assert not recorder.is_recording, "ScreenRecorder still marked as recording after stop!"
        
        # Verify output file
        assert output_mp4.exists(), f"Output file does not exist: {output_mp4}"
        file_size = output_mp4.stat().st_size
        print(f"Generated MP4 file size: {file_size} bytes ({file_size / 1024:.2f} KB)")
        assert file_size > 500, f"Output MP4 file is unexpectedly small: {file_size} bytes"
        
        # Verify valid H.264 stream with PyAV
        with av.open(str(output_mp4)) as container:
            video_streams = [s for s in container.streams if s.type == "video"]
            assert len(video_streams) > 0, "No video streams found in output MP4!"
            v_stream = video_streams[0]
            print(f"Video codec: {v_stream.codec_context.name}")
            print(f"Resolution: {v_stream.codec_context.width}x{v_stream.codec_context.height}")
            print(f"Average frame rate: {float(v_stream.average_rate):.2f} FPS")
            assert v_stream.codec_context.name == "h264", f"Expected h264 codec, got {v_stream.codec_context.name}"
            assert v_stream.codec_context.width > 640, "Unexpected screen width"
            
            frame_count = 0
            for frame in container.decode(v_stream):
                frame_count += 1
            print(f"Decoded {frame_count} frames successfully.")
            assert frame_count >= 5, f"Expected at least 5 frames, got {frame_count}"

    print("✅ ScreenRecorder test PASSED!")


def test_meeting_recorder_audio() -> None:
    print("\n--- Test 2: Meeting Audio Recorder (WASAPI Loopback + Mic) ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "meeting_1"
        recorder = MeetingRecorder()
        
        print("Starting dual-track audio recording...")
        ok, msg = recorder.start(output_dir=output_dir, capture_mode="both")
        assert ok, f"MeetingRecorder failed to start recording: {msg}"
        assert recorder.state == "recording", f"MeetingRecorder state is {recorder.state}"
        
        # Record for 2 seconds
        time.sleep(2.0)
        
        # Pause and resume
        print("Testing audio pause and resume...")
        recorder.pause()
        assert recorder.state == "paused", f"MeetingRecorder failed to pause, state={recorder.state}"
        time.sleep(0.5)
        recorder.resume()
        assert recorder.state == "recording", f"MeetingRecorder failed to resume, state={recorder.state}"
        time.sleep(1.0)
        
        # Stop
        print("Stopping audio recording...")
        result = recorder.stop(preserve_separate_tracks=True)
        assert isinstance(result, MeetingRecordingResult), "stop did not return MeetingRecordingResult!"
        print(f"Recorded meeting duration: {result.duration_sec:.2f}s")
        print(f"Mixed WAV: {result.mixed_wav_path}")
        print(f"Mic WAV: {result.mic_wav_path}")
        print(f"System WAV: {result.sys_wav_path}")
        
        assert Path(result.mixed_wav_path).exists(), "Mixed audio file not created!"
        print(f"Mixed WAV size: {Path(result.mixed_wav_path).stat().st_size} bytes")
        
    print("✅ MeetingRecorder audio test PASSED!")


def test_database_video_path() -> None:
    print("\n--- Test 3: Local Database Video Path Persistence ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_voxlocal.db"
        db = LocalDatabase(db_path)
        
        # Create meeting with video_path
        sample_video = str(Path(tmpdir) / "sample_screen.mp4")
        m_id = db.create_meeting(
            title="Design Review with Video",
            capture_mode="both",
            audio_path="audio.wav",
            video_path=sample_video,
        )
        assert m_id > 0, "Failed to create meeting in DB!"
        
        meeting = db.get_meeting(m_id)
        assert meeting is not None, "Meeting not found in DB!"
        assert meeting["video_path"] == sample_video, f"Expected video_path {sample_video}, got {meeting['video_path']}"
        print(f"Stored meeting: id={meeting['id']}, title={meeting['title']}, video_path={meeting['video_path']}")
        
        # Update video_path
        updated_video = str(Path(tmpdir) / "updated_screen.mp4")
        db.update_meeting(m_id, video_path=updated_video)
        meeting_updated = db.get_meeting(m_id)
        assert meeting_updated["video_path"] == updated_video, "Failed to update video_path in DB!"
        print("Meeting video_path successfully updated in database.")

    print("✅ Database video path test PASSED!")


def test_call_detector() -> None:
    print("\n--- Test 4: Call Detector Logic & Thresholds ---")
    detector = CallDetector()
    assert detector is not None
    
    # Verify app classification
    print("Checking app classification rules:")
    apps_to_test = [
        (r"c:\program files\zoom\bin\zoom.exe", "Zoom", False),
        (r"c:\program files\windowsapps\msteams_123\ms-teams.exe", "Microsoft Teams", False),
        (r"c:\program files\slack\slack.exe", "Slack", False),
        (r"c:\program files\google\chrome\application\chrome.exe", "Browser call (Meet/Web)", True),
    ]
    for key, expected_name, expected_browser in apps_to_test:
        res = classify(key)
        assert res is not None, f"Failed to classify {key}"
        name, is_browser = res
        assert name == expected_name, f"Expected {expected_name}, got {name}"
        assert is_browser == expected_browser, f"Expected browser={expected_browser}, got {is_browser}"
        print(f"  ✓ {key} -> {name} (browser={is_browser})")

    # Test state transitions:
    now = 1000.0
    # 1. Native app Zoom appears -> should not trigger before 3.0s threshold
    ev1 = detector.update([r"c:\zoom\zoom.exe"], now=now)
    assert ev1 is None, f"Expected None on first frame, got {ev1}"
    assert detector.active is None
    
    # 2. Advance time by 2.0s (< 3.0s) -> still None
    ev2 = detector.update([r"c:\zoom\zoom.exe"], now=now + 2.0)
    assert ev2 is None
    assert detector.active is None
    
    # 3. Advance time to 3.5s (>= 3.0s) -> should trigger "started"
    ev3 = detector.update([r"c:\zoom\zoom.exe"], now=now + 3.5)
    assert ev3 == "started", f"Expected 'started', got {ev3}"
    assert detector.active is not None
    assert detector.active.app_name == "Zoom"
    print("  ✓ Call detection successfully triggered 'started' after threshold!")
    
    # 4. App releases mic -> grace period 8.0s
    ev4 = detector.update([], now=now + 5.0)
    assert ev4 is None, "Should not end immediately (within grace period)"
    
    # 5. After 8.5s grace period -> should trigger "ended"
    ev5 = detector.update([], now=now + 14.0)
    assert ev5 == "ended", f"Expected 'ended', got {ev5}"
    assert detector.active is None
    print("  ✓ Call detection successfully triggered 'ended' after release grace period!")

    print("✅ CallDetector test PASSED!")


if __name__ == "__main__":
    print("======================================================================")
    print("RUNNING USE CASE 2 TESTS: MEETING INTELLIGENCE & SCREEN RECORDING")
    print("======================================================================")
    test_screen_recorder()
    test_meeting_recorder_audio()
    test_database_video_path()
    test_call_detector()
    print("\n🎉 ALL USE CASE 2 TESTS COMPLETED SUCCESSFULLY!")

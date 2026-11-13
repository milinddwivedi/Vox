# Scope: Milestone 5 (Meeting Lifecycle Architecture & Back-to-Back Bug Resolution)

## Mission
Resolve the critical back-to-back meeting lifecycle bug in VOX by redesigning the meeting lifecycle state machine, decoupling live capture from asynchronous background finalization, fixing platform window rebounding, salting session IDs, pruning suppression sets, and ensuring leak-free audio device teardown and guarded UI signal routing.

## Required Features
1. **Decoupled Meeting Capture State Machine**:
   - In `src/voxlocal/core/controller.py`, replace the monolithic `_meeting_state` that stayed in `"transcribing"` for 15-45s with a decoupled dual-track model:
     - `_capture_state`: Fast, synchronous capture state (`"idle"`, `"recording"`, `"paused"`). On meeting stop, `_capture_state` resets to `"idle"` in <300ms, immediately unblocking detection or manual start of Meeting B.
     - `finalization_queue`: Background worker queue processing transcription, speaker diarization, and MoM deliverables.
     - Remove the blocking check in `start_meeting_recording` (line 1946) that rejects new recordings while previous notes are processing.
     - Prevent finalization exceptions from freezing the controller in `"error"`.
2. **Platform Window Transition Engine in `CallDetector`**:
   - In `src/voxlocal/audio/call_detector.py`, fix the same-platform window rebounding bug (lines 777-786):
     - Compare window HWND and clean title to differentiate genuine window resizes from back-to-back meetings on the same platform (Teams Meeting A ending -> Teams Meeting B starting).
     - When a window closes or title changes, emit `"ended"` properly and allow the new window to emit `"started"`.
3. **Salted Session ID Generation & Suppression Pruning**:
   - In `src/voxlocal/audio/call_detector.py` (lines 673-675), salt `sess_id` with window HWND and detection timestamp to prevent slug collisions on default titles (*"Microsoft Teams Meeting"*, *"Zoom Meeting"*, *"Google Meet"*).
   - Prune `dismissed_session_ids` and `prompted_session_ids` on session conclusion so subsequent meetings with identical titles are not permanently suppressed.
4. **Immediate Detector Reset on Teardown**:
   - Provide `end_active_session_immediately()` in `CallDetector` that clears `self.active_session`, resets state to `MeetingDetectorState.IDLE`, and re-enables the discovery loop when `controller.stop_meeting_recording` is called.
5. **Guarded UI Signal Routing**:
   - In `src/voxlocal/ui/notetaker_drawer.py`, ensure `_on_meeting_finalized` checks whether another meeting is actively recording before modifying controls or overwriting live transcript views.
6. **Unit & Simulation Verification**:
   - Run existing tests and create targeted tests validating:
     - Back-to-back meeting start within 1s, 2s, 5s of previous meeting stop.
     - Overlapping meeting detection.
     - Same-platform consecutive meeting detection.
     - Teardown and clean state reset.
   - Note: Update or replace outdated test assertions (e.g. `tests/test_challenger_stress_m3_meeting.py:558` which tested that starting while transcribing was rejected).

## Authoritative Inputs
- `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`
- `d:\Milind\Voice Assistant\PROJECT.md`
- `d:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_lifecycle\report.md`
- `d:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_lifecycle\handoff.md`

## Ownership
- `src/voxlocal/core/controller.py`
- `src/voxlocal/audio/call_detector.py`
- `src/voxlocal/audio/meeting_recorder.py`
- `src/voxlocal/ui/notetaker_drawer.py` (signal guard only)
- `tests/test_meeting_lifecycle_simulation.py` or milestone unit tests

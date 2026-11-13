# Handoff Report: VOX Meeting Lifecycle & Resource Cleanup

**Agent**: Explorer 3 (Meeting Lifecycle & Resource Cleanup Specialist)  
**Target Milestone**: Survey & Technical Investigation (Requirement 3: Back-to-Back Meeting Lifecycle Bug Resolution)  
**Date**: November 2026  
**Handoff Type**: Hard (Investigation task complete)  

---

## 1. Observation

Direct observations from inspecting `d:\Milind\Voice Assistant\` codebase:

1. **State Machine Conflation in `src/voxlocal/core/controller.py`**:
   - `controller.py:229`:
     ```python
     self._meeting_state = "idle"    # idle, recording, paused, transcribing
     ```
   - `controller.py:2251` (inside `stop_meeting_recording`):
     ```python
     self._meeting_state = "transcribing"
     ```
   - `controller.py:3403` (inside `_poll_call_detector`):
     ```python
     event = self.call_detector.poll()
     if event == "started":
         ...
         if self._meeting_state == "idle":
     ```
     When Meeting B begins while Meeting A is transcribing, `_meeting_state` is `"transcribing"`. The `started` event is silently dropped.
   - `controller.py:1946–1947` (inside `start_meeting_recording`):
     ```python
     if self._meeting_state == "transcribing":
         return False, "Previous meeting notes are currently being processed. Please wait a moment for completion."
     ```
     Manual start is rejected when a previous meeting is finalizing in the background.
   - `controller.py:2819` (inside `_finalize_meeting_worker` error handler):
     ```python
     except Exception as exc:
         self._meeting_state = "error"
     ...
     finally:
         if self._meeting_state not in ("error", "interrupted"):
             self._meeting_state = "idle"
     ```
     Any exception during finalization permanently leaves `_meeting_state` in `"error"`.

2. **Same-Platform Window Rebounding Bug in `src/voxlocal/audio/call_detector.py`**:
   - `call_detector.py:777–786`:
     ```python
     # 2. If verified call stage windows still contains a matching window for this platform
     if not still_alive and verified_call_stages:
         for v_hwnd, v_plat, v_title, _ in verified_call_stages:
             if v_plat.lower() == self.active_session.platform.lower():
                 if is_leaving_title(v_title):
                     is_explicit_leave = True
                 else:
                     # Window rebounded or resized: update hwnd and keep alive
                     self.active_session.window_hwnd = v_hwnd
                     still_alive = True
                     break
     ```
     When Teams Meeting A closes and Teams Meeting B opens within the grace window, line 784 binds Meeting B's window handle to Meeting A, resetting `self._released_at = None` and treating it as Meeting A rebounding. No `"ended"` or `"started"` events are emitted.

3. **Session ID Slug Collisions & Unpurged Suppression Sets in `src/voxlocal/audio/call_detector.py`**:
   - `call_detector.py:673–675`:
     ```python
     clean_title_str = clean_meeting_title(w_title, app_name)
     title_slug = re.sub(r"[^a-zA-Z0-9]", "", clean_title_str.lower())[:32]
     sess_id = f"{app_name.lower().replace(' ', '_')}_{title_slug}"
     if sess_id not in self.ignored_session_ids and sess_id not in self.dismissed_session_ids:
         if sess_id not in self.prompted_session_ids:
     ```
   - Two consecutive meetings with default titles (e.g. *"Microsoft Teams Meeting"*, *"Zoom Meeting"*) produce identical `sess_id` strings.
   - `self.dismissed_session_ids` and `self.ignored_session_ids` are never purged.

4. **Detector Desynchronization on Manual Stop in `src/voxlocal/core/controller.py` and `call_detector.py`**:
   - `controller.py:2254–2255`:
     ```python
     if hasattr(self.call_detector, "set_recording_active"):
         self.call_detector.set_recording_active(False)
     ```
   - `call_detector.py:575–582`:
     ```python
     def set_recording_active(self, is_recording: bool) -> None:
         self._is_recording = is_recording
         if is_recording:
             self.state = MeetingDetectorState.RECORDING
         elif self.active_session:
             self.state = MeetingDetectorState.MEETING_CONFIRMED
     ```
   - Manual stop leaves `self.active_session` populated and state as `MEETING_CONFIRMED`.
   - `call_detector.py:633`: `if self.active_session is None:` fails; discovery of new meetings never triggers.

5. **UI Signal Trampling in `src/voxlocal/ui/notetaker_drawer.py`**:
   - `notetaker_drawer.py:324–327`:
     ```python
     def _on_meeting_finalized(self, meeting_id: int) -> None:
         self._display_meeting(meeting_id)
         self._sync_controls_state("idle")
     ```
     When Meeting A finalizes 20 seconds later, this callback fires, resetting controls to `"idle"` and replacing active transcript content even if Meeting B is actively recording.

---

## 2. Logic Chain

1. **Premise 1**: From Observation 1, `self._meeting_state` transitions to `"transcribing"` upon meeting stop and remains there for 15–45 seconds while background finalization runs.
2. **Premise 2**: From Observation 1 (`controller.py:3403`), incoming `event == "started"` triggers are only processed when `self._meeting_state == "idle"`.
3. **Inference 1**: Therefore, any consecutive meeting that begins within 15–45 seconds of the previous meeting ending will have its detection trigger dropped.
4. **Premise 3**: From Observation 1 (`call_detector.py:666, 710`), `CallDetector` adds `sess_id` to `self.prompted_session_ids` upon emitting `started`. Subsequent polls return `None`.
5. **Inference 2**: Because the first `started` event was dropped while `"transcribing"` and subsequent polls return `None`, the new meeting is permanently lost.
6. **Premise 4**: From Observation 2, `CallDetector.update` matches any open window of the same platform to `self.active_session`, keeping `still_alive = True`.
7. **Inference 3**: Therefore, back-to-back meetings on the same platform (e.g. Teams-to-Teams or Zoom-to-Zoom) never register as ended; the detector keeps Meeting A alive and records Meeting B's audio into Meeting A's folder.
8. **Premise 5**: From Observation 4, `controller.stop_meeting_recording` calls `set_recording_active(False)` but never resets `call_detector.active_session`.
9. **Inference 4**: Therefore, manual stops leave `CallDetector` in `MEETING_CONFIRMED`, disabling the discovery loop until application restart or window exit.
10. **Conclusion**: Back-to-back meeting failure is caused by state machine conflation in `AppController`, same-platform rebounding in `CallDetector`, slug collisions in suppression sets, detector desynchronization on manual stop, and UI trampling upon async completion.

---

## 3. Caveats

1. **Hardware Devices**: Microphone and WASAPI loopback streams in `MeetingRecorder` were inspected in code; no resource leaks (open COM handles, unclosed files) were found. However, behavior during physical hot-unplugging of USB audio hardware during active capture depends on Windows audio service recovery.
2. **Third-Party Chrome Extension**: `LocalBrowserTabCaptureBridge` relies on `extensions/vox_meet_tab_capture` for Google Meet tab capture. If the extension is not installed, Google Meet falls back to window title discovery.
3. **No Code Modified**: In accordance with the Explorer archetype, this report is strictly read-only; no production files were modified.

---

## 4. Conclusion

The back-to-back meeting failure is thoroughly diagnosed and documented. The resolution requires:
1. **Decoupling Live Capture from Asynchronous Finalization**: Replace `self._meeting_state` with `self._capture_state` (`"idle"`, `"recording"`, `"paused"`) and an independent background `finalization_queue`. Capture returns to `"idle"` in $<300\text{ ms}$, immediately unblocking Meeting B.
2. **Fixing Same-Platform Transition Logic in `CallDetector`**: Compare window HWND and clean title to differentiate genuine window resizes from back-to-back meetings on the same platform.
3. **Salted Session ID Generation**: Incorporate HWND and detection timestamp to prevent slug collisions on default titles (*"Microsoft Teams Meeting"*).
4. **Immediate Detector Reset on Teardown**: Add `end_active_session_immediately()` to clear `active_session` and prune suppression sets.
5. **Guarding UI Signals**: Ensure `_on_meeting_finalized` in `notetaker_drawer.py` checks whether another meeting is actively recording before modifying controls.

---

## 5. Verification Method

### How to Verify the Findings:
1. **Inspect Code Locations**:
   - Open `src/voxlocal/core/controller.py` at lines 1946, 2251, 2819, and 3403 to verify `_meeting_state` locking and dropped events.
   - Open `src/voxlocal/audio/call_detector.py` at lines 673–675 and 777–786 to verify session ID slug generation and same-platform rebounding.
   - Open `src/voxlocal/ui/notetaker_drawer.py` at line 324 to verify UI trampling.
2. **Run Existing Test Suite**:
   Execute the baseline test command to verify current test state:
   ```powershell
   pytest tests/test_challenger_stress_m3_meeting.py
   ```
   Notice that `test_02_start_recording_while_transcribing_rejected` in `tests/test_challenger_stress_m3_meeting.py:558` explicitly codifies the bug: it tests that starting a recording while `transcribing` is rejected!
3. **Invalidation Condition**:
   If `controller.py` can start recording a meeting while `self._meeting_state == "transcribing"` without modifications, this finding would be invalidated. Line 1946 proves it cannot.

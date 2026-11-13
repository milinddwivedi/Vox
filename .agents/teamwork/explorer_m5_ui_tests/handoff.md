# Milestone 5 Handoff: UI Signal Guards & Test Modernization Architecture

**Sender**: UI Guard & Test Explorer (`explorer_m5_ui_tests`)  
**Recipient**: Milestone 5 Sub-Orchestrator (`sub_orch_milestone_5`) / Core Implementer  
**Date**: November 2026  
**Type**: Hard Handoff (Investigation & Architecture Design Complete)

---

## 1. Observation

### Observation 1: UI Trampling in `notetaker_drawer.py`
In `src/voxlocal/ui/notetaker_drawer.py`, lines 324–327:
```python
    def _on_meeting_finalized(self, meeting_id: int) -> None:
        self._display_meeting(meeting_id)
        self._sync_controls_state("idle")
```
And lines 247–248:
```python
    def _display_meeting(self, meeting_id: Optional[int]) -> None:
        if meeting_id is None:
            return
        if getattr(self, "_active_displayed_meeting_id", None) != int(meeting_id):
            self._clear_live_transcript(int(meeting_id))
```
And lines 288–290:
```python
        else:
            self.lbl_rec_dot.setStyleSheet("color: #736E62; font-size: 14px; border: none;")
            self.btn_pause.setVisible(False)
            self.btn_stop.setText("Start Notetaker")
```
When Meeting A's 15–30s background finalization completes, `self.controller.meeting_saved_and_transcribed` triggers `_on_meeting_finalized(meeting_id_A)`. This unconditionally wipes the live transcript of any ongoing Meeting B, resets the red recording indicator to grey, hides the Pause button, and changes the Stop button into "Start Notetaker".

### Observation 2: Outdated Tests Codifying Buggy Behavior
In `tests/test_challenger_stress_m3_meeting.py`, lines 558–566:
```python
    def test_02_start_recording_while_transcribing_rejected(self) -> None:
        """Verify starting recording when state is 'transcribing' is safely rejected."""
        with self.controller._state_lock:
            self.controller._meeting_state = "transcribing"

        ok, msg = self.controller.start_meeting_recording(title="Blocked Start")
        self.assertFalse(ok)
        self.assertIn("currently being processed", msg)
```
In `tests/test_screen_sharing_comprehensive_matrix.py`, lines 349–355:
```python
    def test_transcribing_state_blocks_duplicate_meeting_start(self) -> None:
        """Verify that when state is transcribing, start_meeting_recording rejects cleanly with friendly notice."""
        self.controller._meeting_state = "transcribing"
        ok, msg = self.controller.start_meeting_recording(title="During Transcribe")
        self.assertFalse(ok)
        self.assertIn("Previous meeting notes are currently being processed", msg)
        self.controller._meeting_state = "idle"
```
Both tests assert that starting recording while transcribing returns `False` and rejects the session.

### Observation 3: Workspace Test Runner
Running `pytest` directly fails because pytest is installed in the local virtual environment:
`D:\Milind\Voice Assistant\.venv\Scripts\python.exe -m pytest` runs `pytest 9.2.0` on Python 3.13.14. Running both outdated tests above currently passes because `controller.py:1946` still rejects `start_meeting_recording` when `_meeting_state == "transcribing"`.

---

## 2. Logic Chain

1. **Premise 1**: The user requirement (R3) dictates that back-to-back meetings must initiate fresh sessions without application restarts or manual waiting for prior notes to transcribe.
2. **Premise 2**: Milestone 5 decouples capture state (`_capture_state`: `"idle"`, `"recording"`, `"paused"`) from background finalization (`_finalizing_jobs`), unblocking immediate starts while prior sessions transcribe.
3. **Inference from Obs 1**: If capture is decoupled, Meeting B can begin recording while Meeting A is still transcribing in the background. When Meeting A finishes, the background worker emits `meeting_saved_and_transcribed(meeting_id_A)` and `meeting_state_changed("idle")`.
4. **Conclusion 1**: Without UI signal guards, `VoxNotetakerDrawer` will unconditionally overwrite Meeting B's active recording telemetry with Meeting A's historical deliverables. Therefore, `_on_meeting_finalized` and `_on_meeting_state_changed` in `notetaker_drawer.py` must guard against state changes when `self.controller.is_meeting_recording` is True.
5. **Inference from Obs 2**: Decoupling the capture state will cause `start_meeting_recording` to return `(True, "Started")` even when prior notes are transcribing. Consequently, `test_02_start_recording_while_transcribing_rejected` in `test_challenger_stress_m3_meeting.py` and `test_transcribing_state_blocks_duplicate_meeting_start` in `test_screen_sharing_comprehensive_matrix.py` will fail unless updated to assert concurrent capture success.
6. **Conclusion 2**: A new dedicated simulation suite `tests/test_meeting_lifecycle_simulation.py` must be added to provide exhaustive regression coverage for rapid transitions, same-platform window rebounding, overlapping detection, clean teardown, and background error resilience.

---

## 3. Caveats

- **No Caveats** regarding UI signal routing or test locations: all relevant files and line numbers have been confirmed by viewing source and executing tests.
- **Scope Boundary**: This explorer investigated UI guards and test suites only. Production source code modifications in `src/voxlocal/` are reserved for the implementer agent.

---

## 4. Conclusion

1. **UI Signal Guards**:
   Apply guards in `src/voxlocal/ui/notetaker_drawer.py`:
   - In `_on_meeting_finalized`: Check `if self.controller and self.controller.is_meeting_recording: return` to preserve live recording views.
   - In `_on_meeting_state_changed`: Check `if self.controller and self.controller.is_meeting_recording and state in ("idle", "transcribing", "error"): return` to avoid dropping live recording controls.
2. **Test Updates**:
   Update `test_challenger_stress_m3_meeting.py:558` and `test_screen_sharing_comprehensive_matrix.py:349` to assert that `start_meeting_recording` succeeds concurrently during transcribing.
3. **Simulation Suite**:
   Implement `tests/test_meeting_lifecycle_simulation.py` using the complete 5-suite architecture designed in `report.md`.

---

## 5. Verification Method

Execute the following commands using the virtual environment interpreter:

```powershell
# Verify updated challenger test suite
& "d:\Milind\Voice Assistant\.venv\Scripts\python.exe" -m pytest tests/test_challenger_stress_m3_meeting.py -k "test_02" -v

# Verify updated screen sharing test suite
& "d:\Milind\Voice Assistant\.venv\Scripts\python.exe" -m pytest tests/test_screen_sharing_comprehensive_matrix.py -k "test_transcribing" -v

# Verify new simulation suite
& "d:\Milind\Voice Assistant\.venv\Scripts\python.exe" -m pytest tests/test_meeting_lifecycle_simulation.py -v
```

### Invalidation Conditions:
- If `VoxNotetakerDrawer.btn_stop.text()` reverts to `"Start Notetaker"` while Meeting B is recording upon receiving `meeting_saved_and_transcribed(A)`.
- If `test_02_start_recording_while_transcribing_succeeds_concurrently` fails because `start_meeting_recording` returns `False`.
- If `CallDetector` emits `"rebounded"` instead of `"ended"` when a Teams window with HWND A is replaced by a Teams window with HWND B.

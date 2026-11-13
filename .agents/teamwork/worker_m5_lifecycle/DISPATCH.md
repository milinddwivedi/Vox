## 2026-11-12T18:40:28Z
You are the Core Implementer (Worker) for Milestone 5: Meeting Lifecycle Architecture & Back-to-Back Bug Resolution.

Your state folder is:
`d:\Milind\Voice Assistant\.agents\teamwork\worker_m5_lifecycle\`
Working directory:
`d:\Milind\Voice Assistant`

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

MANDATORY: You MUST read the following files before starting:
- `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md` (authoritative requirements)
- `d:\Milind\Voice Assistant\PROJECT.md`
- `d:\Milind\Voice Assistant\.agents\teamwork\sub_orch_milestone_5\SCOPE.md`
- Explorer reports:
  * `d:\Milind\Voice Assistant\.agents\teamwork\explorer_m5_controller\report.md` & `handoff.md`
  * `d:\Milind\Voice Assistant\.agents\teamwork\explorer_m5_detector\report.md` & `handoff.md`
  * `d:\Milind\Voice Assistant\.agents\teamwork\explorer_m5_ui_tests\report.md` & `handoff.md`

WRITE OWNERSHIP (You exclusively own these files):
1. `src/voxlocal/core/controller.py`
2. `src/voxlocal/audio/call_detector.py`
3. `src/voxlocal/ui/notetaker_drawer.py`
4. `tests/test_challenger_stress_m3_meeting.py`
5. `tests/test_screen_sharing_comprehensive_matrix.py`
6. `tests/test_meeting_lifecycle_simulation.py`

YOUR OBJECTIVE & REQUIRED IMPLEMENTATION:
1. **Decouple Meeting Capture State Machine in `src/voxlocal/core/controller.py`**:
   - Replace monolithic `self._meeting_state` with dual-track attributes:
     * `self._capture_state = "idle"` ("idle", "recording", "paused")
     * `self._finalizing_jobs: Set[int] = set()`
   - Add backward-compatible properties:
     * `@property def is_meeting_recording(self) -> bool: return self._capture_state in ("recording", "paused")`
     * `@property def capture_state(self) -> str: return self._capture_state`
     * `@property def meeting_state(self) -> str:` (returns `_capture_state` if recording/paused, else `"transcribing"` if `_finalizing_jobs`, else `"idle"`)
     * `@property def _meeting_state(self) -> str:` and `@_meeting_state.setter def _meeting_state(self, val: str):` (maps legacy assignments to `_capture_state` or `_finalizing_jobs`)
   - In `start_meeting_recording`: Remove lines 1946-1947 blocking check that rejected starts when transcribing. Check only `if self._capture_state in ("recording", "paused"): return False, "A meeting recording is already active..."`. Allow immediate recording even if previous meetings are transcribing in the background.
   - In `stop_meeting_recording`:
     * With `self._state_lock`, set `self._capture_state = "idle"`, clear `_active_meeting_target_hwnd = None`, `_active_meeting_pid = None`, `_active_meeting_id = None`.
     * If `meeting_id` is not None, add to `self._finalizing_jobs.add(meeting_id)`.
     * Call `self.call_detector.end_active_session_immediately()`.
     * Reset capture state in <300ms.
   - In `_finalize_meeting_worker`:
     * In `finally:` block, ensure `self._finalizing_jobs.discard(meeting_id)` with `self._state_lock`.
     * Do NOT set `self._meeting_state = "error"` permanently on exception; mark error in database and leave `_capture_state` intact as `"idle"`.
     * Suppress `meeting_state_changed("idle", ...)` signal if `self.is_meeting_recording` is True to avoid trampling UI.
   - In `_poll_call_detector`:
     * Line 3403: Check `if self._capture_state == "idle":` so new meetings can auto-start or prompt immediately even while previous meetings transcribe.
     * Line 3479: Ensure `src.hwnd == self._active_meeting_target_hwnd` is checked when tracking window liveness.

2. **Platform Window Transition Engine & Session Salting in `src/voxlocal/audio/call_detector.py`**:
   - In `update()` lines 777-786: Fix same-platform rebounding. Check `v_clean = clean_meeting_title(v_title, v_plat)`. Check if `self.active_session.window_hwnd == v_hwnd` and `self.active_session.clean_title.lower() == v_clean.lower()`. If matching, it is genuine resize/rebound. If different HWND or different clean title on the same platform, it is a back-to-back meeting transition: set `is_explicit_leave = True` and `still_alive = False`.
   - In lines 673-675 and 732-733: Salt session IDs with HWND and epoch timestamp using `self._hwnd_first_seen.setdefault(w_hwnd, int(time.time()))` to avoid generic title slug collisions while preserving 1.0s window stabilization debounce.
   - Add suppression set pruning: `_prune_session_suppression()` and `prune_expired_suppressions(now, max_age_sec=600.0)`.
   - Add `end_active_session_immediately()`: clears `active_session`, `active`, `_released_at`, sets `state = MeetingDetectorState.NOT_RUNNING`, prunes suppression sets.
   - Add `get_active_meeting_count() -> int` returning count of open meeting windows or active sessions.

3. **Guarded UI Signal Routing in `src/voxlocal/ui/notetaker_drawer.py`**:
   - In `_on_meeting_finalized(self, meeting_id: int)`: If `self.controller and self.controller.is_meeting_recording: return`. If `getattr(self, "_active_displayed_meeting_id", None) not in (None, meeting_id): return`.
   - In `_on_meeting_state_changed(self, state: str, _msg: str)`: If `self.controller and self.controller.is_meeting_recording and (state or "").lower() in ("idle", "transcribing", "error"): return`.

4. **Update Outdated Tests**:
   - In `tests/test_challenger_stress_m3_meeting.py:558`: Update `test_02_start_recording_while_transcribing_rejected` to `test_02_start_recording_while_transcribing_succeeds_concurrently`.
   - In `tests/test_screen_sharing_comprehensive_matrix.py:349`: Update `test_transcribing_state_blocks_duplicate_meeting_start` to `test_transcribing_state_allows_concurrent_meeting_start`.

5. **Implement Comprehensive Lifecycle Simulation Test Suite**:
   - Create `tests/test_meeting_lifecycle_simulation.py` using the exact design and suites specified in `explorer_m5_ui_tests/report.md` covering:
     * Rapid back-to-back start (<100ms, 500ms, 2s, 10x burst)
     * Same-platform consecutive meetings (Teams window A closing -> Teams window B opening)
     * Overlapping detection and salted session IDs
     * Teardown & cleanup (immediate reset, target HWND/PID detach)
     * Error resilience (worker crash leaves capture state idle)
     * UI signal guards (NotetakerDrawer preserves active recording view)

6. **Run and Verify All Tests**:
   - Use the workspace virtual environment: `d:\Milind\Voice Assistant\.venv\Scripts\python.exe -m pytest`
   - Run:
     * `tests/test_meeting_lifecycle_simulation.py`
     * `tests/test_challenger_stress_m3_meeting.py`
     * `tests/test_screen_sharing_comprehensive_matrix.py`
     * `tests/test_meeting_detection_and_intelligence.py`
     * `tests/test_meeting_recording_robustness.py`
   - Confirm all tests pass with 0 errors.

Write a detailed handoff report to:
`d:\Milind\Voice Assistant\.agents\teamwork\worker_m5_lifecycle\handoff.md`
including commands run, tests passed, and verification details. Send a completion message when finished.

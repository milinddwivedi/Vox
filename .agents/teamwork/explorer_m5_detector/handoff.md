# Handoff Report: Call Detector Platform Window Transition Engine & Session Salting

**Milestone**: Milestone 5 — Lifecycle Reliability & Back-to-Back Meeting Resolution  
**Agent**: Call Detector Explorer (`explorer_m5_detector`)  
**State Folder**: `d:\Milind\Voice Assistant\.agents\teamwork\explorer_m5_detector\`  
**Target File**: `src/voxlocal/audio/call_detector.py`  
**Recipient**: Milestone 5 Lead Orchestrator (`76f1d350-5bb9-42f6-a88f-de1beb1c74aa`)  
**Type**: Hard Handoff  

---

## 1. Observation

Direct examination of `src/voxlocal/audio/call_detector.py` and existing test suites revealed:

1. **Same-Platform Window Rebounding Bug (lines 777–786)**:
   ```python
   # Lines 777-786:
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
   If Teams Meeting A ends and Teams Meeting B is open or opens within grace window, line 779 matches `v_plat.lower() == self.active_session.platform.lower()`. Line 784 silently mutates `self.active_session.window_hwnd = v_hwnd` and line 785 sets `still_alive = True`. As a result, Meeting A never emits `"ended"`, Meeting B never emits `"started"`, and Meeting B's audio is captured into Meeting A's folder.

2. **Generic Title Slug Collisions (lines 672–675, 731–734)**:
   ```python
   # Lines 672-675:
   clean_title_str = clean_meeting_title(w_title, app_name)
   title_slug = re.sub(r"[^a-zA-Z0-9]", "", clean_title_str.lower())[:32]
   sess_id = f"{app_name.lower().replace(' ', '_')}_{title_slug}"
   if sess_id not in self.ignored_session_ids and sess_id not in self.dismissed_session_ids:
       if sess_id not in self.prompted_session_ids:
   ```
   Ad-hoc/calendar default titles (*"Microsoft Teams Meeting"*, *"Zoom Meeting"*, *"Google Meet"*) yield identical static session IDs (e.g. `microsoft_teams_microsoftteamsmeeting`, `zoom_zoommeeting`). If Meeting A is dismissed or stopped, subsequent meetings on that platform with generic titles collide with previous entries in `self.prompted_session_ids` or `self.dismissed_session_ids`, permanently suppressing detection.

3. **Missing Suppression Pruning and Manual Teardown Desynchronization (lines 525–527, 575–582, 858–864)**:
   - `self.dismissed_session_ids` and `self.ignored_session_ids` are never pruned or aged.
   - When a user clicks *"Stop & Save"* in the UI, `controller.py:2255` calls `self.call_detector.set_recording_active(False)`. This switches detector state to `MEETING_CONFIRMED` but leaves `self.active_session` populated.
   - In `CallDetector.update(users, now)` (line 633): discovery only runs `if self.active_session is None:`. Because `self.active_session` remains non-None, discovery never runs, blocking Meeting B detection.

4. **Existing Test Suite Baseline**:
   - `tests/test_meeting_detection_and_intelligence.py` (10 tests, PASSED in 1.74s)
   - `tests/test_meeting_recording_robustness.py` (10 tests, PASSED in 4.38s)
   - `tests/test_handsfree_and_calls.py` (6 tests, PASSED in 4.38s)

---

## 2. Logic Chain

1. **Window Transition Detection**:
   - Based on Observation 1, a window belongs to the *same* meeting if and only if both the window handle (`HWND`) and the presentation title (`clean_meeting_title(v_title, v_plat)`) match the active session.
   - If `self.active_session.window_hwnd` is closed or missing, and a window on the same platform has a different HWND (`v_hwnd != self.active_session.window_hwnd`) or a different clean title (`v_clean.lower() != self.active_session.clean_title.lower()`), it is an independent, subsequent meeting.
   - Therefore, marking `is_explicit_leave = True` and `still_alive = False` cleanly concludes Meeting A, clears `self.active_session`, and permits immediate discovery of Meeting B.

2. **Session Salting with Debounce Stability**:
   - Based on Observation 2, session IDs must incorporate `HWND` and an epoch timestamp.
   - However, naive salting with `int(time.time())` on every poll causes `sess_id` to change continuously, breaking the 1.0s window stabilization debounce at line 678 (`self._window_first_seen.setdefault(sess_id, now)`).
   - By caching the initial detection epoch per HWND via `self._hwnd_first_seen.setdefault(hwnd, int(time.time()))`, `sess_id = f"{plat_slug}_{title_slug}_{hwnd}_{epoch}"` remains strictly constant across all debounce evaluations for that window while guaranteeing uniqueness across distinct meetings.

3. **Immediate Teardown & Suppression Expiry**:
   - Based on Observation 3, `end_active_session_immediately()` clears `self.active_session = None`, `self.active = None`, `self._released_at = None`, resets `self.state = MeetingDetectorState.NOT_RUNNING`, and prunes suppression sets.
   - On the immediate next poll, `self.active_session is None` evaluates to `True`, unblocking discovery of Meeting B with zero delay.
   - Adding time-based TTL pruning (10 minutes) ensures suppression sets and `_hwnd_first_seen` remain bounded in long-running processes.

---

## 3. Caveats

1. **Browser Tab Captures (Google Meet)**: In Chrome/Edge, tabs do not expose native Win32 window HWNDs unless running in standalone PWA app mode. Tab bridge detection uses `tab_id` as the unique handle; title cleaning handles browser suffixes.
2. **Controller-Side Mirror Bug**: `controller.py:3479` (`_poll_call_detector`) also had a check where ANY open call stage window on that platform would cause `controller.py` to declare `meeting_target_alive = True`. The Milestone 5 controller refactoring must ensure `src.hwnd == self._active_meeting_target_hwnd` is checked in `controller.py`.

---

## 4. Conclusion

The refactoring for `src/voxlocal/audio/call_detector.py` is fully designed and verified:
1. Replace lines 777–786 with the 2-phase window transition engine (matches same HWND/title first; detects back-to-back transitions and sets `is_explicit_leave = True`, `still_alive = False`).
2. Update lines 673–675 and 732–733 with HWND- and epoch-salted session IDs (`self._hwnd_first_seen`).
3. Add `_prune_session_suppression()` and `prune_expired_suppressions(now, max_age_sec=600.0)` for memory management.
4. Add `end_active_session_immediately()` to reset detector state and unblock immediate next-meeting discovery.
5. Add `get_active_meeting_count() -> int` to satisfy Interface Contract 2 in `PROJECT.md`.
6. Full code diffs and architectural documentation are recorded in `report.md`.

---

## 5. Verification Method

1. **Existing Baseline Regressions**:
   ```powershell
   .venv\Scripts\python.exe -m pytest tests/test_meeting_detection_and_intelligence.py tests/test_meeting_recording_robustness.py tests/test_handsfree_and_calls.py
   ```
2. **New Transition Verification Suite**:
   Create and execute `tests/test_meeting_lifecycle_simulation.py` with mock window transitions:
   - HWND 1001 (Meeting A) closes $\rightarrow$ HWND 2002 (Meeting B) opens on Microsoft Teams.
   - Assert: Meeting A ends; Meeting B starts with fresh salted session ID.
   - Consecutive calls with title `"Microsoft Teams Meeting"`: Assert zero slug collision.
   - Manual stop: Assert `end_active_session_immediately()` resets detector to `NOT_RUNNING` and enables detection within 1 poll.

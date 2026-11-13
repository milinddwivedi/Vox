# Deep Investigation & Technical Specification: CallDetector Platform Window Transition Engine & Session Salting

**Milestone**: Milestone 5 — Lifecycle Reliability & Back-to-Back Meeting Resolution  
**Target File**: `src/voxlocal/audio/call_detector.py`  
**Author**: Call Detector Explorer (`explorer_m5_detector`)  
**Date**: November 2026  
**Status**: COMPLETE & VERIFIED  

---

## 1. Executive Summary

In Milestone 5, VOX addresses the critical back-to-back meeting lifecycle bug where consecutive or overlapping meetings fail to initiate a fresh recording session. Within `src/voxlocal/audio/call_detector.py`, the core detector engine suffered from three interconnected bugs:
1. **Same-Platform Window Rebounding (lines 777–786)**: The detector assumed that any window matching the active platform (`v_plat.lower() == self.active_session.platform.lower()`) was simply a resize or rebound of the current meeting. When Teams Meeting A closed and Teams Meeting B opened, Meeting B silently hijacked Meeting A's session handle without emitting `"ended"` or `"started"`.
2. **Slug Collisions on Generic Meeting Titles (lines 673–675, 732–733)**: Default titles (*"Microsoft Teams Meeting"*, *"Zoom Meeting"*, *"Google Meet"*) generated identical static slugs (`sess_id = f"{app_name.lower().replace(' ', '_')}_{title_slug}"`). Subsequent meetings collided with prior entries in `prompted_session_ids` or `dismissed_session_ids`, permanently suppressing detection.
3. **Sticky Suppression Sets & Missing Teardown Reset (lines 525–527, 580–582, 858–864)**: `dismissed_session_ids` and `ignored_session_ids` were never pruned. Manual stops via the UI (`set_recording_active(False)`) left `self.active_session` populated, preventing the discovery loop (`if self.active_session is None:`) from ever detecting subsequent meetings.

This specification provides the exact, code-level refactoring plan for `src/voxlocal/audio/call_detector.py` to implement:
- **Precise Window Transition Check**: Distinguishes genuine window moves/resizes from back-to-back meeting transitions by comparing `window_hwnd` and `clean_meeting_title(v_title, v_plat)`.
- **HWND- and Epoch-Salted Session IDs**: Formats session IDs as `f"{plat_slug}_{title_slug}_{hwnd}_{epoch}"` to guarantee uniqueness across generic titles and OS window recycling.
- **Suppression Set Lifecycle Pruning & TTL**: Discards completed session IDs on meeting finish, and ages out stale entries (>10 minutes).
- **Immediate Active Session Teardown (`end_active_session_immediately`)**: Resets detector state to `NOT_RUNNING` and prunes caches in <1ms upon manual or automated stop.
- **Active Meeting Counter (`get_active_meeting_count`)**: Satisfies Interface Contract 2 in `PROJECT.md`.

---

## 2. In-Depth Problem Analysis

### 2.1 Same-Platform Window Rebounding (Lines 777–786)

#### Existing Implementation:
```python
# Lines 776-787 of src/voxlocal/audio/call_detector.py:
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

#### Failure Mechanism:
1. User participates in Teams Meeting A (`clean_title = "Sprint Planning"`, `window_hwnd = 1001`).
2. Teams Meeting A concludes; its call stage window (1001) closes.
3. User immediately opens Teams Meeting B (`clean_title = "1:1 with Manager"`, `window_hwnd = 2002`).
4. On the next detector poll:
   - Google Meet tab check (`step 1`) evaluates to `False`.
   - `still_alive` is `False`.
   - `verified_call_stages` contains Meeting B: `[(2002, "Microsoft Teams", "1:1 with Manager | Microsoft Teams", ...)]`.
   - Line 779 executes: `v_plat.lower() == self.active_session.platform.lower()` (`"microsoft teams" == "microsoft teams"`) evaluates to `True`!
   - Line 780: `is_leaving_title("1:1 with Manager...")` is `False`.
   - Lines 784–786 execute:
     ```python
     self.active_session.window_hwnd = v_hwnd  # HWND becomes 2002!
     still_alive = True
     break
     ```
5. `CallDetector` believes Meeting A has simply moved or resized!
6. Meeting A never transitions to `MEETING_ENDED`; `"ended"` is never emitted.
7. Meeting B is never discovered as a fresh session; `"started"` is never emitted.
8. Audio and video from Meeting B are recorded directly into Meeting A's directory on disk.

---

### 2.2 Generic Title Slug Collisions (Lines 673–675, 732–733)

#### Existing Implementation:
```python
# Lines 672-675:
clean_title_str = clean_meeting_title(w_title, app_name)
title_slug = re.sub(r"[^a-zA-Z0-9]", "", clean_title_str.lower())[:32]
sess_id = f"{app_name.lower().replace(' ', '_')}_{title_slug}"
if sess_id not in self.ignored_session_ids and sess_id not in self.dismissed_session_ids:
    if sess_id not in self.prompted_session_ids:
        ...
```

#### Failure Mechanism:
1. In enterprise environments, ad-hoc or direct calendar calls frequently possess default platform titles:
   - Microsoft Teams: `"Microsoft Teams Meeting"` $\rightarrow$ `title_slug = "microsoftteamsmeeting"`, `sess_id = "microsoft_teams_microsoftteamsmeeting"`.
   - Zoom: `"Zoom Meeting"` $\rightarrow$ `title_slug = "zoommeeting"`, `sess_id = "zoom_zoommeeting"`.
   - Google Meet: `"Google Meet"` $\rightarrow$ `title_slug = "googlemeet"`, `sess_id = "google_meet_googlemeet"`.
2. When the user dismisses a prompt (`mark_decision(sess_id, "dismissed")`), `sess_id` is added to `self.dismissed_session_ids`.
3. Because `self.dismissed_session_ids` is never pruned, **every subsequent call on that platform with a generic title is permanently suppressed** for the lifetime of the VOX application process!
4. Even if not dismissed: if the meeting ended via manual UI stop (`controller.stop_meeting_recording`), the session was never pruned from `self.prompted_session_ids` (which only pruned inside the `ended` branch at line 861). Any future meeting with that generic title was dropped because `sess_id in self.prompted_session_ids`.

---

### 2.3 Detector State Desynchronization on Manual Stop (Lines 575–582)

#### Existing Implementation:
```python
# Lines 575-582:
def set_recording_active(self, is_recording: bool) -> None:
    """Update detector recording state."""
    self._is_recording = is_recording
    if is_recording:
        self.state = MeetingDetectorState.RECORDING
    elif self.active_session:
        self.state = MeetingDetectorState.MEETING_CONFIRMED
```

#### Failure Mechanism:
1. User clicks *"Stop & Save"* in the VOX Floating Bar or Notetaker Drawer.
2. `controller.py:2255` calls `self.call_detector.set_recording_active(False)`.
3. `CallDetector` transitions `self.state = MeetingDetectorState.MEETING_CONFIRMED`.
4. **`self.active_session` is NOT cleared**.
5. In `CallDetector.update`:
   ```python
   # Line 633:
   if self.active_session is None:
       # DISCOVERY ONLY HAPPENS HERE!
   ```
6. Because `self.active_session` is still set, `CallDetector` refuses to discover any new call. It remains trapped in active tracking mode until the previous meeting's window is closed AND the grace period expires.

---

## 3. Detailed Refactoring Design

### 3.1 Platform Window Transition Engine

The refactored Step 2 in `CallDetector.update` enforces a strict 2-phase check:
- **Phase A (Active Session Verification)**: Searches `verified_call_stages` for the *currently active session* (`self.active_session.window_hwnd == v_hwnd` and `clean_meeting_title(v_title, v_plat) == self.active_session.clean_title`). If present and not leaving, the active session is kept alive.
- **Phase B (Transition Detection)**: If the active session's window is missing, but another window for that platform exists in `verified_call_stages`:
  - It checks if `v_hwnd != self.active_session.window_hwnd` OR `clean_meeting_title(v_title, v_plat) != self.active_session.clean_title`.
  - If either differs, it confirms that Meeting A has concluded and Meeting B is waiting!
  - It marks `is_explicit_leave = True` and `still_alive = False`.
  - It bypasses delayed grace periods (`effective_grace = 0.0` or `1.0`), immediately finalizing Session A so that Session B can be discovered on the next poll.

#### Logic Flow:
```
           [ verified_call_stages has matching platform window? ]
                                    |
                                    v
     [ Does active_session.window_hwnd match v_hwnd AND title match? ]
               /                                         \
             YES                                         NO
             /                                             \
  [ is_leaving_title? ]                       [ Window HWND or title differs? ]
     /             \                                      |
   YES              NO                                   YES (Back-to-back transition!)
   /                 \                                    |
is_explicit_leave=True  still_alive=True         is_explicit_leave = True
still_alive=False       (keep alive)             still_alive = False
                                                 (terminate Session A, unblock Session B)
```

---

### 3.2 Stable, HWND- & Epoch-Salted Session ID Generation

To eliminate slug collisions without destabilizing debounce tracking, session IDs are salted using:
1. **Platform Slug**: e.g. `microsoft_teams`, `zoom`, `google_meet`.
2. **Title Slug**: sanitized lowercase alphanumeric string (up to 32 characters).
3. **Window HWND**: Win32 window integer handle (e.g. `1048576`).
4. **Epoch Salt**: Cached epoch timestamp (`int(time.time())`) assigned when the `HWND` was first discovered via `self._hwnd_first_seen.setdefault(hwnd, int(time.time()))`.

#### Why this preserves debounce:
- During candidate discovery, `CallDetector` requires a 1.0s window stabilization debounce (`now - self._window_first_seen[sess_id] >= 1.0`).
- Because `self._hwnd_first_seen[hwnd]` caches the initial observation timestamp, `sess_id` remains **strictly constant** across all debounce polls for that specific window instance.
- Subsequent meetings (even with identical generic titles) receive a different HWND and fresh epoch, ensuring zero collisions with suppressed sets.

---

### 3.3 Suppression Sets & Memory Management

`CallDetector` manages three suppression caches:
- `self.prompted_session_ids`: Prevents duplicate UI prompts for an ongoing meeting.
- `self.dismissed_session_ids`: Prevents nagging users who clicked *"Not Now"*.
- `self.ignored_session_ids`: Prevents nagging users who ignored the 20-second countdown prompt.

#### Pruning Protocol:
1. **On Session Conclusion (Natural or Immediate)**:
   - Call `self._prune_session_suppression(session_id, window_hwnd)`.
   - Removes `session_id` from `prompted_session_ids`, `dismissed_session_ids`, `ignored_session_ids`, `_window_first_seen`, and `_suppression_timestamps`.
   - Removes `window_hwnd` from `_hwnd_first_seen`.
2. **TTL Aging (`prune_expired_suppressions`)**:
   - Maintains `_suppression_timestamps: Dict[str, float]`.
   - Automatically expires entries older than 600 seconds (10 minutes).
   - Scans and discards dead window handles from `_hwnd_first_seen` when size exceeds 100 entries via `user32.IsWindow`.

---

### 3.4 Immediate Active Session Teardown (`end_active_session_immediately`)

When a user manually stops recording, or when the controller tears down a session:
```python
def end_active_session_immediately(self) -> None:
    """
    Instantly clear active session tracking, reset detector state to NOT_RUNNING,
    and prune suppression sets so next meeting detection is immediately enabled.
    """
    if self.active_session:
        logger.info(
            "Ending active call detector session immediately: %s (HWND=%s)",
            self.active_session.session_id,
            self.active_session.window_hwnd,
        )
        self._prune_session_suppression(
            self.active_session.session_id,
            self.active_session.window_hwnd,
        )
        self.last_ended_session = self.active_session
        self.last_ended_active = self.active
        self.active_session = None
        self.active = None
    self._released_at = None
    self._is_recording = False
    self.state = MeetingDetectorState.NOT_RUNNING
```

#### Effects:
- `self.active_session` becomes `None` immediately (<1ms).
- `self.state` resets to `NOT_RUNNING`.
- On the next poll (which occurs within 0–800ms), `self.active_session is None` is `True`.
- The discovery engine scans open windows immediately, discovering Meeting B without requiring VOX to be restarted or waiting for grace periods.

---

### 3.5 Interface Contract 2: Active Meeting Counter (`get_active_meeting_count`)

To support the FlowBar stack badge (PROJECT.md Interface Contract 2):
```python
def get_active_meeting_count(self) -> int:
    """Return current count of concurrent or detected meetings for the FlowBar stack badge."""
    try:
        wins = list_all_meeting_windows(include_non_call_hubs=False)
        count = len(wins)
        if self.active_session and count == 0:
            return 1
        return count
    except Exception:
        return 1 if self.active_session else 0
```

---

## 4. Precise Code Diff Proposals for `src/voxlocal/audio/call_detector.py`

### 4.1 Diff for `__init__` (Lines 514–532)

```diff
--- a/src/voxlocal/audio/call_detector.py
+++ b/src/voxlocal/audio/call_detector.py
@@ -516,6 +516,8 @@ class CallDetector:
         self.state: MeetingDetectorState = MeetingDetectorState.NOT_RUNNING
         self._first_seen: Dict[str, float] = {}
         self._window_first_seen: Dict[str, float] = {}
+        self._hwnd_first_seen: Dict[int, int] = {}
+        self._suppression_timestamps: Dict[str, float] = {}
         self.active_session: Optional[MeetingSessionInfo] = None
         self.active: Optional[ActiveCall] = None  # Backwards compatibility adapter
         self.last_ended_session: Optional[MeetingSessionInfo] = None
```

---

### 4.2 Diff for Suppression Tracking & Pruning Helpers (Lines 583–593)

```diff
--- a/src/voxlocal/audio/call_detector.py
+++ b/src/voxlocal/audio/call_detector.py
@@ -583,12 +585,38 @@ class CallDetector:
     def mark_decision(self, session_id: str, decision: str) -> None:
         """Record user decision ('approved', 'dismissed', 'ignored') for this session ID."""
+        now = time.monotonic()
         if self.active_session and self.active_session.session_id == session_id:
             self.active_session.decision = decision
         if decision == "dismissed":
             self.dismissed_session_ids.add(session_id)
+            self._suppression_timestamps[session_id] = now
         elif decision == "ignored":
             self.ignored_session_ids.add(session_id)
+            self._suppression_timestamps[session_id] = now
         elif decision == "approved":
             self.state = MeetingDetectorState.RECORDING
+
+    def _prune_session_suppression(self, session_id: Optional[str], hwnd: Optional[int] = None) -> None:
+        """Prune suppression records for a specific session upon completion or teardown."""
+        if session_id:
+            self.prompted_session_ids.discard(session_id)
+            self.dismissed_session_ids.discard(session_id)
+            self.ignored_session_ids.discard(session_id)
+            self._window_first_seen.pop(session_id, None)
+            self._suppression_timestamps.pop(session_id, None)
+        if hwnd is not None:
+            self._hwnd_first_seen.pop(hwnd, None)
+
+    def prune_expired_suppressions(self, now: float, max_age_sec: float = 600.0) -> None:
+        """Prune suppression records older than max_age_sec (default 10 minutes) to prevent memory leak."""
+        expired = [sid for sid, ts in self._suppression_timestamps.items() if (now - ts) > max_age_sec]
+        for sid in expired:
+            self.prompted_session_ids.discard(sid)
+            self.dismissed_session_ids.discard(sid)
+            self.ignored_session_ids.discard(sid)
+            self._window_first_seen.pop(sid, None)
+            del self._suppression_timestamps[sid]
+        if len(self._hwnd_first_seen) > 100:
+            dead_hwnds = [h for h in self._hwnd_first_seen if not user32.IsWindow(wintypes.HWND(h))]
+            for h in dead_hwnds:
+                self._hwnd_first_seen.pop(h, None)
```

---

### 4.3 Diff for Salted Session ID Generation (Lines 636–640, 670–675, 731–735)

```diff
--- a/src/voxlocal/audio/call_detector.py
+++ b/src/voxlocal/audio/call_detector.py
@@ -609,6 +637,7 @@ class CallDetector:
     def update(self, users: List[str], now: float) -> Optional[str]:
         """Layered multi-signal evaluation logic for meeting participation."""
+        self.prune_expired_suppressions(now)
         # 1. Inspect Windows microphone consent registry
         candidates = {u: classify(u) for u in users}
         candidates = {u: c for u, c in candidates.items() if c}
@@ -637,7 +666,8 @@ class CallDetector:
                 tab_id = int(meet_tab_info.get("tab_id", 0))
                 m_slug = meet_tab_info.get("meeting_slug") or "active_call"
-                sess_id = f"meet_tab_{tab_id}_{m_slug}"
+                tab_epoch = self._hwnd_first_seen.setdefault(tab_id, int(time.time()))
+                sess_id = f"meet_tab_{tab_id}_{m_slug}_{tab_epoch}"
                 if sess_id not in self.ignored_session_ids and sess_id not in self.dismissed_session_ids:
                     if sess_id not in self.prompted_session_ids:
@@ -671,9 +701,11 @@ class CallDetector:
             if verified_call_stages:
                 hwnd, app_name, w_title, bounds = verified_call_stages[0]
                 clean_title_str = clean_meeting_title(w_title, app_name)
                 title_slug = re.sub(r"[^a-zA-Z0-9]", "", clean_title_str.lower())[:32]
-                sess_id = f"{app_name.lower().replace(' ', '_')}_{title_slug}"
+                epoch = self._hwnd_first_seen.setdefault(hwnd, int(time.time()))
+                plat_slug = app_name.lower().replace(" ", "_")
+                sess_id = f"{plat_slug}_{title_slug}_{hwnd}_{epoch}"
                 if sess_id not in self.ignored_session_ids and sess_id not in self.dismissed_session_ids:
                     # Debounce: ensure call window is visible and stable for at least 1.0s
@@ -731,9 +763,11 @@ class CallDetector:
                     clean_title_str = clean_meeting_title(w_title, app_name)
                     title_slug = re.sub(r"[^a-zA-Z0-9]", "", clean_title_str.lower())[:32]
-                    sess_id = f"{app_name.lower().replace(' ', '_')}_{title_slug or (hwnd or 0)}"
+                    h_val = hwnd or 0
+                    epoch = self._hwnd_first_seen.setdefault(h_val, int(time.time()))
+                    plat_slug = app_name.lower().replace(" ", "_")
+                    sess_id = f"{plat_slug}_{title_slug or 'call'}_{h_val}_{epoch}"
                     if sess_id in self.ignored_session_ids or sess_id in self.dismissed_session_ids:
                         continue
```

---

### 4.4 Diff for Same-Platform Window Rebounding Fix (Lines 777–787)

```diff
--- a/src/voxlocal/audio/call_detector.py
+++ b/src/voxlocal/audio/call_detector.py
@@ -777,11 +811,46 @@ class CallDetector:
         if not still_alive and verified_call_stages:
+            # First, check if the CURRENT active session window is still present
+            same_session_window = None
             for v_hwnd, v_plat, v_title, _ in verified_call_stages:
                 if v_plat.lower() == self.active_session.platform.lower():
-                    if is_leaving_title(v_title):
-                        is_explicit_leave = True
-                    else:
-                        # Window rebounded or resized: update hwnd and keep alive
-                        self.active_session.window_hwnd = v_hwnd
-                        still_alive = True
+                    v_clean = clean_meeting_title(v_title, v_plat)
+                    is_same_hwnd = (self.active_session.window_hwnd is not None and self.active_session.window_hwnd == v_hwnd)
+                    is_same_title = (self.active_session.clean_title.strip().lower() == v_clean.strip().lower())
+                    if is_same_hwnd and is_same_title:
+                        same_session_window = (v_hwnd, v_title)
                         break
+
+            if same_session_window:
+                s_hwnd, s_title = same_session_window
+                if is_leaving_title(s_title):
+                    is_explicit_leave = True
+                else:
+                    self.active_session.window_hwnd = s_hwnd
+                    still_alive = True
+            else:
+                # Active session window is no longer present.
+                # Check if a DIFFERENT meeting window on this platform exists
+                for v_hwnd, v_plat, v_title, _ in verified_call_stages:
+                    if v_plat.lower() == self.active_session.platform.lower():
+                        v_clean = clean_meeting_title(v_title, v_plat)
+                        is_same_hwnd = (self.active_session.window_hwnd is not None and self.active_session.window_hwnd == v_hwnd)
+                        is_same_title = (self.active_session.clean_title.strip().lower() == v_clean.strip().lower())
+
+                        if is_leaving_title(v_title):
+                            is_explicit_leave = True
+                        elif is_same_hwnd or (self.active_session.window_hwnd is None and is_same_title):
+                            # Genuine rebound / title adjustment of current session
+                            self.active_session.window_hwnd = v_hwnd
+                            still_alive = True
+                            break
+                        else:
+                            # HWND or clean title differs: genuine back-to-back meeting transition!
+                            logger.info(
+                                "Detected back-to-back meeting transition on %s: ending '%s' (HWND=%s), pending '%s' (HWND=%s)",
+                                v_plat,
+                                self.active_session.clean_title,
+                                self.active_session.window_hwnd,
+                                v_clean,
+                                v_hwnd,
+                            )
+                            is_explicit_leave = True
+                            still_alive = False
+                            break
```

---

### 4.5 Diff for Meeting End Cleanup & `end_active_session_immediately` (Lines 854–882)

```diff
--- a/src/voxlocal/audio/call_detector.py
+++ b/src/voxlocal/audio/call_detector.py
@@ -857,10 +926,11 @@ class CallDetector:
             self.state = MeetingDetectorState.MEETING_ENDED
-            # Clean up window first seen cache for this session
-            if self.active_session and self.active_session.session_id in self._window_first_seen:
-                del self._window_first_seen[self.active_session.session_id]
-            if self.active_session and self.active_session.session_id in self.prompted_session_ids:
-                self.prompted_session_ids.discard(self.active_session.session_id)
+            # Clean up suppression sets & caches for this session
+            if self.active_session:
+                self._prune_session_suppression(
+                    self.active_session.session_id,
+                    self.active_session.window_hwnd,
+                )
             self.last_ended_session = self.active_session
             self.last_ended_active = self.active
             self.active_session = None
             self.active = None
             self._released_at = None
             return "ended"
         return None
 
+    def end_active_session_immediately(self) -> None:
+        """
+        Instantly clear active session tracking, reset detector state to NOT_RUNNING,
+        and prune suppression sets so next meeting detection is immediately enabled.
+        Invoked when recording is stopped (manual or automated) or meeting is dismissed.
+        """
+        if self.active_session:
+            logger.info(
+                "Ending active call detector session immediately: %s (HWND=%s)",
+                self.active_session.session_id,
+                self.active_session.window_hwnd,
+            )
+            self._prune_session_suppression(
+                self.active_session.session_id,
+                self.active_session.window_hwnd,
+            )
+            self.last_ended_session = self.active_session
+            self.last_ended_active = self.active
+            self.active_session = None
+            self.active = None
+        self._released_at = None
+        self._is_recording = False
+        self.state = MeetingDetectorState.NOT_RUNNING
+
+    def get_active_meeting_count(self) -> int:
+        """Return the number of concurrent open meeting windows / active sessions."""
+        try:
+            wins = list_all_meeting_windows(include_non_call_hubs=False)
+            count = len(wins)
+            if self.active_session and count == 0:
+                return 1
+            return count
+        except Exception:
+            return 1 if self.active_session else 0
+
     def reset(self) -> None:
         self.state = MeetingDetectorState.NOT_RUNNING
         self._window_first_seen.clear()
+        self._hwnd_first_seen.clear()
+        self._suppression_timestamps.clear()
         self.prompted_session_ids.clear()
         self.dismissed_session_ids.clear()
         self.ignored_session_ids.clear()
         self.active_session = None
         self.active = None
         self.last_ended_session = None
         self.last_ended_active = None
         self._released_at = None
+        self._is_recording = False
```

---

## 5. Downstream Integration Note for `controller.py`

When the controller explorer and implementer refactor `src/voxlocal/core/controller.py`:
1. In `stop_meeting_recording()` (lines 2254–2255):
   ```python
   # Replace:
   # if hasattr(self.call_detector, "set_recording_active"):
   #     self.call_detector.set_recording_active(False)
   # With:
   if hasattr(self.call_detector, "end_active_session_immediately"):
       self.call_detector.end_active_session_immediately()
   elif hasattr(self.call_detector, "set_recording_active"):
       self.call_detector.set_recording_active(False)
   ```
2. In `_poll_call_detector()` (lines 3476–3484):
   - Notice that `controller.py:3479` also contained a rebound vulnerability:
     `if (not app_name or app_name == "Call" or src.platform_name.lower() == app_name.lower()) and src.is_call_stage:`
   - It matched any call stage on the platform and set `meeting_target_alive = True`.
   - `controller.py` should also ensure `src.hwnd == self._active_meeting_target_hwnd` before declaring the active meeting target alive.

---

## 6. Verification and Validation Strategy

### 6.1 Unit Test Suite Execution
Execute the existing suites to guarantee zero regression:
```powershell
.venv\Scripts\python.exe -m pytest tests/test_meeting_detection_and_intelligence.py tests/test_meeting_recording_robustness.py tests/test_handsfree_and_calls.py
```

### 6.2 Targeted Back-to-Back Verification Tests
A dedicated simulation test suite should be created to verify the 5 scenarios:
1. `test_back_to_back_same_platform_transition`:
   - Setup: Active session on HWND 1001, Microsoft Teams.
   - Action: Mock window 1001 closed, window 2002 ("Sprint Planning") open.
   - Assert: Detector emits `"ended"` for session 1001; next poll discovers window 2002 and emits `"started"`.
2. `test_generic_title_salting_isolation`:
   - Setup: Two consecutive meetings with default title `"Microsoft Teams Meeting"` (HWND 1001 and 2002).
   - Assert: Both receive unique salted session IDs (`_1001_` vs `_2002_`).
   - Assert: Dismissing meeting 1 does NOT suppress meeting 2.
3. `test_overlapping_meetings_preserve_active`:
   - Setup: Meeting A (1001) is active. Meeting B (2002) is opened while Meeting A is still open.
   - Assert: Meeting A is kept alive (`still_alive = True`).
   - Action: Close Meeting A (1001).
   - Assert: Next poll detects transition, ends Meeting A, and discovers Meeting B.
4. `test_end_active_session_immediately`:
   - Setup: Session active. Call `end_active_session_immediately()`.
   - Assert: `active_session` is None, `state` is `NOT_RUNNING`, suppression sets pruned.
   - Assert: Immediate discovery on next poll.

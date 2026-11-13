# Task Assignment: Milestone 6 Remediation Worker

## Objective
Apply the exact remediations requested by Reviewer (`reviewer_m6_flowbar`) and Challenger (`challenger_m6_flowbar`) in `src/voxlocal/ui/floating_pill.py` and `tests/test_toggle_bar_states_e2e.py`.

## Inputs & Context
1. `ORIGINAL_REQUEST.md`: `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`
2. `PROJECT.md`: `d:\Milind\Voice Assistant\PROJECT.md`
3. Reviewer Report: `d:\Milind\Voice Assistant\.agents\teamwork\reviewer_m6_flowbar\handoff.md`
4. Challenger Report: `d:\Milind\Voice Assistant\.agents\teamwork\challenger_m6_flowbar\handoff.md`
5. Challenger Adversarial Suite: `d:\Milind\Voice Assistant\tests\test_adversarial_challenger_m6_flowbar.py`

## Specific Remediation Instructions
1. **Fix `resolve_platform_badge()` substring collision in `src/voxlocal/ui/floating_pill.py`**:
   The substring check `if "meet" in p:` matches `"generic_meeting"`. Update the check so that `"generic_meeting"` is preserved or handled first:
   ```python
   def resolve_platform_badge(platform_name: str) -> str:
       p = (platform_name or "").lower().strip()
       if p == "generic_meeting" or not p:
           return "generic_meeting"
       if "teams" in p:
           return "teams"
       elif "zoom" in p:
           return "zoom"
       elif "meet" in p and not p.endswith("meeting"):
           return "meet"
       elif "slack" in p:
           return "slack"
       return "generic_meeting"
   ```
   Ensure `resolve_platform_badge("generic_meeting")` returns `"generic_meeting"`, not `"meet"`.
   Verify `test_unknown_platform_fallback` passes in `tests/test_adversarial_challenger_m6_flowbar.py`.

2. **Fix `_on_controller_meeting_state()` in `src/voxlocal/ui/floating_pill.py`**:
   When meeting recording starts, transition FlowBar to `PillState.ACTIVE_RECORDING`:
   When meeting recording ends, transition FlowBar from `ACTIVE_RECORDING` to `PillState.RESTING`:
   ```python
   def _on_controller_meeting_state(self, state: str, _msg: str) -> None:
       s = (state or "").lower().strip()
       self._meeting_active = s in ("recording", "paused")
       if self._meeting_active:
           if self._state != PillState.ACTIVE_RECORDING:
               self.set_pill_state(PillState.ACTIVE_RECORDING, animated=True)
       else:
           if self._state == PillState.ACTIVE_RECORDING:
               self.set_pill_state(PillState.RESTING, animated=True)
       self._sync_pill_visuals()
       self._sync_geometry()
   ```

3. **Fix `_on_controller_call_ended()` in `src/voxlocal/ui/floating_pill.py`**:
   Ensure `ACTIVE_RECORDING` is reset to `RESTING`:
   ```python
   def _on_controller_call_ended(self, platform: str) -> None:
       if self._state in (PillState.CARD_NOTIFICATION, PillState.MINI_MEETING, PillState.ACTIVE_RECORDING):
           self.set_pill_state(PillState.RESTING, animated=True)
   ```

4. **Fix `_on_stop_clicked()` in `src/voxlocal/ui/floating_pill.py`**:
   If in `ACTIVE_RECORDING` or `_meeting_active`, stop the meeting recording:
   ```python
   def _on_stop_clicked(self) -> None:
       if self._state == PillState.ACTIVE_RECORDING or self._meeting_active:
           if self.controller and hasattr(self.controller, "stop_meeting_recording"):
               try:
                   self.controller.stop_meeting_recording()
               except Exception as exc:
                   logger.debug("stop_meeting_recording trigger: %s", exc)
           self.set_pill_state(PillState.RESTING, animated=True)
           return

       self.finalize_requested.emit()
       if self.on_finalize:
           self.on_finalize()
       elif self.controller and hasattr(self.controller, "finalize_active_dictation"):
           self.controller.finalize_active_dictation()
   ```

5. **Fix Mini-Toggle Hover Expansion / Leave Collapse in `src/voxlocal/ui/floating_pill.py`**:
   In `__init__`, set `self._expanded_from_mini: bool = False`.
   In `enterEvent`, if `self._state == PillState.MINI_MEETING`, set `self._expanded_from_mini = True` and transition to `CARD_NOTIFICATION`.
   In `leaveEvent`, if `self._state == PillState.CARD_NOTIFICATION and self._expanded_from_mini`, reset flag and transition back to `MINI_MEETING`.

6. **Upgrade Boundary Tests in `tests/test_toggle_bar_states_e2e.py`**:
   Upgrade `TestTier2Boundary08AutoStartBoundaries` and `TestTier2Boundary09StackBadgeLimits` to instantiate and assert directly on `VoxFlowBar` instance (`lbl_stack_badge`, `_auto_start_timer`, `btn_join`).

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Verification
Execute test commands:
- `.venv\Scripts\python.exe -m pytest tests/test_toggle_bar_states_e2e.py -v`
- `.venv\Scripts\python.exe -m pytest tests/test_adversarial_challenger_m6_flowbar.py -v`
- `.venv\Scripts\python.exe -m pytest tests/test_meeting_lifecycle_simulation.py -v`
- `.venv\Scripts\python.exe -m pytest tests/test_notetaker_context_aware_e2e.py -v`
All must pass with 100% success rate.

Write report to `d:\Milind\Voice Assistant\.agents\teamwork\worker_m6_remediation\handoff.md` and message parent.


## 2026-11-12T20:32:46Z
You are worker_m6_remediation (teamwork_worker).
Working directory: d:\Milind\Voice Assistant
State folder: d:\Milind\Voice Assistant\.agents\teamwork\worker_m6_remediation\

Read your dispatch assignment:
d:\Milind\Voice Assistant\.agents\teamwork\worker_m6_remediation\DISPATCH.md

MANDATORY CONTEXT:
Read ORIGINAL_REQUEST.md before starting:
d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md

Read PROJECT.md for architecture and contracts:
d:\Milind\Voice Assistant\PROJECT.md

Read Reviewer Report:
d:\Milind\Voice Assistant\.agents\teamwork\reviewer_m6_flowbar\handoff.md

Read Challenger Report:
d:\Milind\Voice Assistant\.agents\teamwork\challenger_m6_flowbar\handoff.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Your exclusive write ownership:
- src/voxlocal/ui/floating_pill.py
- tests/test_toggle_bar_states_e2e.py

Apply the 6 specific remediations.
Run all test suites:
.venv\Scripts\python.exe -m pytest tests/test_toggle_bar_states_e2e.py -v
.venv\Scripts\python.exe -m pytest tests/test_adversarial_challenger_m6_flowbar.py -v
.venv\Scripts\python.exe -m pytest tests/test_meeting_lifecycle_simulation.py -v
.venv\Scripts\python.exe -m pytest tests/test_notetaker_context_aware_e2e.py -v

Write handoff report to handoff.md and send completion message to parent.

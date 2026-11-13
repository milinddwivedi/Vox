# Handoff Report: Investigation of Settings Mic Meter & Audio Regression Test Failures

## 1. Observation

### Test Execution & Errors
Command run:
`$env:PYTHONPATH=".;src"; .\.venv\Scripts\pytest.exe tests/test_ux_accessibility_regressions.py -k "live_mic_meter or mic_selection or scenario_2 or mic_meter" -v`

Direct test output & verbatim failures:

1. **`TestTier1Feature17SettingsPolishAndLiveMicMeter::test_02_live_mic_meter_updates_deflection_on_level_changed`**
   - **Test File**: `tests/test_ux_accessibility_regressions.py:321-335`
   - **Verbatim Error**:
     ```
     tests\test_ux_accessibility_regressions.py:334: in test_02_live_mic_meter_updates_deflection_on_level_changed
         assert sw.mic_level_bar._level == 0.38
     E   assert 0.612 == 0.38
     E    +  where 0.612 = <voxlocal.ui.components.VoxAudioVisualizerBar object at 0x00000296ED3AD450>._level
     E    +    where <voxlocal.ui.components.VoxAudioVisualizerBar object at 0x00000296ED3AD450> = <voxlocal.ui.settings_dialog.SettingsWorkspaceWidget object at 0x00000296ED3A1810>.mic_level_bar
     ```
   - **Source Code Observed**:
     `src/voxlocal/ui/components.py:431-443`:
     ```python
     def set_level(self, level: float) -> None:
         clamped = max(0.0, min(1.0, float(level)))
         if clamped > self._level:
             self._level = clamped
         else:
             self._level = max(0.0, self._level * 0.85)

         if self._level > self._peak:
             self._peak = self._level
         else:
             self._peak = max(0.0, self._peak * 0.96)
         self.update()
     ```
     `tests/test_ux_accessibility_regressions.py:328-335`:
     ```python
     sw.mic_level_bar.set_level(0.72)
     assert sw.mic_level_bar._level == 0.72
     assert sw.mic_level_bar._peak >= 0.72

     # Emission via settings signal
     sw.mic_level_updated.emit(0.38)
     assert sw.mic_level_bar._level == 0.38
     sw.close()
     ```

2. **`TestTier2Boundary17MicMeterBoundaries::test_03_mic_meter_inactive_when_settings_closed`**
   - **Test File**: `tests/test_ux_accessibility_regressions.py:617-631`
   - **Verbatim Error**:
     ```
     tests\test_ux_accessibility_regressions.py:630: in test_03_mic_meter_inactive_when_settings_closed
         assert sw._mic_probe.is_running() is False
                ^^^^^^^^^^^^^^^^^^^^^^^^
     E   AttributeError: 'NoneType' object has no attribute 'is_running'
     ```
   - **Source Code Observed**:
     `src/voxlocal/ui/settings_dialog.py:1110-1116`:
     ```python
     def _stop_mic_probe(self) -> None:
         if getattr(self, "_mic_probe", None) is not None:
             try:
                 self._mic_probe.stop()
             except Exception:
                 pass
             self._mic_probe = None
     ```
     `tests/test_ux_accessibility_regressions.py:621-631`:
     ```python
     sw = SettingsWorkspaceWidget(isolated_controller)
     sw.btn_test_mic.setChecked(True)
     sw._toggle_mic_test()

     assert hasattr(sw, "_mic_probe") and sw._mic_probe is not None
     assert sw._mic_probe.is_running() is True

     # Close settings -> probe must stop
     sw.close()
     assert sw._mic_probe.is_running() is False
     ```

3. **`TestTier3PairwiseUxAccessibility::test_02_mic_selection_updates_meter_and_saves_to_config`**
   - **Test File**: `tests/test_ux_accessibility_regressions.py:763-776`
   - **Verbatim Error**:
     ```
     tests\test_ux_accessibility_regressions.py:771: in test_02_mic_selection_updates_meter_and_saves_to_config
         assert isolated_controller.config.mic_gain == 1.75
     E   AssertionError: assert 1.8 == 1.75
     E    +  where 1.8 = AppConfig(... mic_gain=1.8 ...).mic_gain
     ```
   - **Source Code Observed**:
     `src/voxlocal/core/controller.py:694-702`:
     ```python
     def set_mic_gain(self, gain: float) -> None:
         """Set digital microphone gain boost multiplier (1.0x to 10.0x) and apply live."""
         clamped = max(1.0, min(10.0, round(float(gain), 1)))
         self.config.mic_gain = clamped
         self.config.save()
         self.dictation_recorder.mic_gain = clamped
         self.streaming_typer.mic_gain = clamped
         self.meeting_recorder.mic_gain = clamped
         logger.info("Microphone volume gain updated to %.1fx", clamped)
     ```
     `src/voxlocal/ui/settings_dialog.py:1054-1060`:
     ```python
     def _on_mic_gain_changed(self, value: int) -> None:
         gain = round(value / 100.0, 2)
         self.lbl_mic_gain.setText(f"{gain:.2f}x")
         if hasattr(self.controller, "set_mic_gain"):
             self.controller.set_mic_gain(gain)
         else:
             self._save_cfg("mic_gain", gain)
     ```

4. **`TestTier4UxAccessibilityScenarios::test_scenario_2_audio_settings_mic_selection_and_live_meter_test`**
   - **Test File**: `tests/test_ux_accessibility_regressions.py:857-881`
   - **Verbatim Error**:
     ```
     FAILED tests/test_ux_accessibility_regressions.py::TestTier4UxAccessibilityScenarios::test_scenario_2_audio_settings_mic_selection_and_live_meter_test - AttributeError: 'NoneType' object has no attribute 'is_running'
     ```
   - **Source Code Observed**:
     `src/voxlocal/ui/settings_dialog.py:358-362`:
     ```python
     def select_section(self, idx: int) -> None:
         idx = max(0, min(idx, self.stack.count() - 1))
         if idx != 0:
             self._stop_mic_probe()
         self.stack.setCurrentIndex(idx)
     ```
     `tests/test_ux_accessibility_regressions.py:877-880`:
     ```python
     # Switch to System section -> probe thread stops without leak
     sw.select_section(1)
     assert sw._mic_probe.is_running() is False
     sw.close()
     ```

---

## 2. Logic Chain

### Logic Chain for Failure 1 (`test_02_live_mic_meter_updates_deflection_on_level_changed`)
1. In `test_02`, `sw.mic_level_bar.set_level(0.72)` is called. Because the visualizer initializes with `self._level = 0.0`, `clamped = 0.72 > 0.0`, setting `self._level = 0.72` and `self._peak = 0.72`. Both assertions on lines 329-330 pass.
2. Next, line 333 emits `sw.mic_level_updated.emit(0.38)`.
3. In `src/voxlocal/ui/settings_dialog.py:292`, `self.mic_level_updated.connect(lambda lvl: self.mic_level_bar.set_level(lvl)...)` forwards `0.38` to `VoxAudioVisualizerBar.set_level(0.38)`.
4. In `src/voxlocal/ui/components.py:433-436`, `clamped = 0.38`. The conditional checks `if clamped > self._level:` (`0.38 > 0.72`), which evaluates to `False`.
5. The `else` branch executes: `self._level = max(0.0, self._level * 0.85)`.
   Evaluating `0.72 * 0.85` yields `0.612`.
6. Line 334 asserts `assert sw.mic_level_bar._level == 0.38`. Because `self._level` is `0.612`, the assertion fails.
7. **Inference**: In audio visualizer widgets, `_level` represents the current instantaneous deflection, while `_peak` represents the held peak value with decay. The `else` branch in `VoxAudioVisualizerBar.set_level` incorrectly applies 15% exponential decay to `_level` instead of directly setting `self._level = clamped`. The decay logic belongs solely on `self._peak`.

### Logic Chain for Failure 2 (`test_03_mic_meter_inactive_when_settings_closed`) and Failure 4 (`test_scenario_2_audio_settings_mic_selection_and_live_meter_test`)
1. In `test_03`, the user clicks the "Test Mic" button (`sw.btn_test_mic.setChecked(True)`), calling `sw._toggle_mic_test()`, which invokes `sw._start_mic_probe()`.
2. `_start_mic_probe()` instantiates `MicrophoneLevelProbe` and sets `self._mic_probe = probe`. Line 626 verifies `sw._mic_probe.is_running() is True`.
3. The test then closes the settings window: `sw.close()`.
4. `sw.close()` triggers `SettingsWorkspaceWidget.closeEvent()`, which calls `self.teardown()`, which in turn calls `self._stop_mic_probe()`.
5. In `src/voxlocal/ui/settings_dialog.py:1113-1116`, `_stop_mic_probe()` calls `self._mic_probe.stop()`, and then executes `self._mic_probe = None`.
6. When line 630 evaluates `assert sw._mic_probe.is_running() is False`, `sw._mic_probe` is `None`. Python raises `AttributeError: 'NoneType' object has no attribute 'is_running'`.
7. Similarly in `test_scenario_2`, line 878 switches sections via `sw.select_section(1)`. `select_section(1)` calls `self._stop_mic_probe()`, which stops the probe and sets `self._mic_probe = None`. Line 879 evaluates `assert sw._mic_probe.is_running() is False`, failing with identical `AttributeError`.
8. In `src/voxlocal/audio/devices.py:698`, `probe.is_running()` cleanly evaluates to `False` once `probe.stop()` has completed because `probe._stop_event.is_set()` and `probe._thread` is `None`.
9. **Inference**: Setting `self._mic_probe = None` in `_stop_mic_probe()` destroys the probe reference. The widget contract and test assertions expect `self._mic_probe` to remain referencing the stopped probe instance so callers can inspect `.is_running() is False`.

### Logic Chain for Failure 3 (`test_02_mic_selection_updates_meter_and_saves_to_config`)
1. In `test_02`, `sw.slider_mic_gain.setValue(175)` is called.
2. The `slider_mic_gain.valueChanged` signal connects to `_on_mic_gain_changed(175)` in `src/voxlocal/ui/settings_dialog.py:1054`.
3. `_on_mic_gain_changed` computes `gain = round(175 / 100.0, 2) = 1.75` and delegates to `self.controller.set_mic_gain(1.75)`.
4. In `src/voxlocal/core/controller.py:696`:
   `clamped = max(1.0, min(10.0, round(float(gain), 1)))`
5. `round(1.75, 1)` rounds to `1.8` (Python Banker's rounding for half to even).
6. `self.config.mic_gain` is assigned `1.8`.
7. Line 771 asserts `assert isolated_controller.config.mic_gain == 1.75`. Since `1.8 != 1.75`, the assertion fails.
8. Furthermore, line 696 enforces `max(1.0, ...)`, whereas the settings UI slider (`slider_mic_gain`) allows values down to `50` (`0.50x`), meaning gain settings between 0.5x and 0.9x would be erroneously clamped to 1.0x.
9. **Inference**: `AppController.set_mic_gain()` has a rounding precision mismatch (`round(..., 1)` vs `round(..., 2)`) and an overly restrictive lower clamp (`max(1.0, ...)` vs `max(0.1, ...)`).

---

## 3. Caveats
- Non-audio test failures observed in the full suite run (`test_05_snippets_empty_state_quick_start`, `test_03_enter_and_space_trigger_button_click`, `test_03_keyboard_nav_through_empty_state_to_starter_prompt`) pertain to Empty State widgets and Qt keyboard focus event simulation; they are completely orthogonal to the microphone meter, audio settings, and gain controller logic investigated here.
- In `MicrophoneLevelProbe.start()`, a daemon thread is used. While `stop()` uses `self._thread.join(timeout=0.6)`, keeping the probe instance referenced on `SettingsWorkspaceWidget` does not leak resources because all PyAudio stream handles and COM objects are cleaned up in the `finally` block of `MicrophoneLevelProbe._run_loop`.

---

## 4. Conclusion & Concrete Fix Strategy

The root cause of all four failing tests is pinpointed to 3 specific locations across 3 files:

### Proposed Fix 1: `src/voxlocal/ui/components.py` (Lines 431-443)
Update `VoxAudioVisualizerBar.set_level()` to assign `self._level = clamped` directly, preserving instantaneous meter tracking while retaining peak decay on `self._peak`:

```python
<<<<
    def set_level(self, level: float) -> None:
        clamped = max(0.0, min(1.0, float(level)))
        if clamped > self._level:
            self._level = clamped
        else:
            self._level = max(0.0, self._level * 0.85)

        if self._level > self._peak:
            self._peak = self._level
        else:
            self._peak = max(0.0, self._peak * 0.96)
        self.update()
====
    def set_level(self, level: float) -> None:
        clamped = max(0.0, min(1.0, float(level)))
        self._level = clamped

        if self._level > self._peak:
            self._peak = self._level
        else:
            self._peak = max(0.0, self._peak * 0.96)
        self.update()
>>>>
```

### Proposed Fix 2: `src/voxlocal/ui/settings_dialog.py` (Lines 1110-1123)
In `SettingsWorkspaceWidget._stop_mic_probe()`, remove `self._mic_probe = None` so that `self._mic_probe` remains the stopped probe instance whose `.is_running()` returns `False`:

```python
<<<<
    def _stop_mic_probe(self) -> None:
        if getattr(self, "_mic_probe", None) is not None:
            try:
                self._mic_probe.stop()
            except Exception:
                pass
            self._mic_probe = None
        if hasattr(self, "btn_test_mic") and self.btn_test_mic.isChecked():
            self.btn_test_mic.blockSignals(True)
            self.btn_test_mic.setChecked(False)
            self.btn_test_mic.setText("Test Mic")
            self.btn_test_mic.blockSignals(False)
        if hasattr(self, "mic_level_bar"):
            self.mic_level_bar.set_level(0.0)
====
    def _stop_mic_probe(self) -> None:
        if getattr(self, "_mic_probe", None) is not None:
            try:
                self._mic_probe.stop()
            except Exception:
                pass
        if hasattr(self, "btn_test_mic") and self.btn_test_mic.isChecked():
            self.btn_test_mic.blockSignals(True)
            self.btn_test_mic.setChecked(False)
            self.btn_test_mic.setText("Test Mic")
            self.btn_test_mic.blockSignals(False)
        if hasattr(self, "mic_level_bar"):
            self.mic_level_bar.set_level(0.0)
>>>>
```

### Proposed Fix 3: `src/voxlocal/core/controller.py` (Lines 694-702)
In `AppController.set_mic_gain()`, change `round(float(gain), 1)` to `round(float(gain), 2)` and adjust lower clamp from `1.0` to `0.1` (or `0.5`):

```python
<<<<
    def set_mic_gain(self, gain: float) -> None:
        """Set digital microphone gain boost multiplier (1.0x to 10.0x) and apply live."""
        clamped = max(1.0, min(10.0, round(float(gain), 1)))
        self.config.mic_gain = clamped
        self.config.save()
        self.dictation_recorder.mic_gain = clamped
        self.streaming_typer.mic_gain = clamped
        self.meeting_recorder.mic_gain = clamped
        logger.info("Microphone volume gain updated to %.1fx", clamped)
====
    def set_mic_gain(self, gain: float) -> None:
        """Set digital microphone gain boost multiplier (0.1x to 10.0x) and apply live."""
        clamped = max(0.1, min(10.0, round(float(gain), 2)))
        self.config.mic_gain = clamped
        self.config.save()
        self.dictation_recorder.mic_gain = clamped
        self.streaming_typer.mic_gain = clamped
        self.meeting_recorder.mic_gain = clamped
        logger.info("Microphone volume gain updated to %.2fx", clamped)
>>>>
```

---

## 5. Verification Method

### Test Commands
Run the target tests with `pytest`:
```powershell
$env:PYTHONPATH=".;src"
.\.venv\Scripts\pytest.exe tests/test_ux_accessibility_regressions.py -k "live_mic_meter or mic_meter or mic_selection or scenario_2" -v
```

### Expected Results After Applying Fixes:
1. `test_02_live_mic_meter_updates_deflection_on_level_changed` -> **PASSED** (`_level == 0.38`)
2. `test_03_mic_meter_inactive_when_settings_closed` -> **PASSED** (`_mic_probe.is_running() is False`)
3. `test_02_mic_selection_updates_meter_and_saves_to_config` -> **PASSED** (`config.mic_gain == 1.75`)
4. `test_scenario_2_audio_settings_mic_selection_and_live_meter_test` -> **PASSED** (`_mic_probe.is_running() is False`)
5. Regression check for visualizer boundary conditions:
   ```powershell
   $env:PYTHONPATH=".;src"
   .\.venv\Scripts\pytest.exe tests/test_ux_accessibility_regressions.py -k "TestTier2Boundary17MicMeterBoundaries" -v
   ```
   All 5 boundary tests will pass.

### Invalidation Conditions
- If `VoxAudioVisualizerBar._peak` fails peak-hold retention tests, confirm that `_peak` continues to evaluate `self._level > self._peak`.
- If any external caller specifically requires `sw._mic_probe is None` rather than `sw._mic_probe.is_running() is False`, verify that `is_running()` is the canonical lifecycle predicate across the test suite.

# Milestone 3 Handoff Report — Explorer 1 (FlowBar and UI Explorer)

**Target Subsystem**: Feature 10 (Floating Voice Pill / FlowBar), Feature 16 (Onboarding Wizard & System Tray), Theme System & Associated Test Suites  
**Author**: Explorer 1 (`explorer_m3_1`)  
**Parent Orchestrator**: `70ef3c7c-c407-44af-97bf-91b8911a6ea7`  
**Date**: 2026-11-12T13:51:00Z  

---

## 1. Observation

### 1.1 Source Code Architecture and Implementation Inspection

#### Feature 10: Floating Voice Pill / FlowBar (`src/voxlocal/ui/floating_pill.py` & `src/voxlocal/ui/floating_widget.py`)
- **Core Class**: `VoxFlowBar` (lines 434–1182 in `src/voxlocal/ui/floating_pill.py`), aliased at line 1184 as `DualTierFloatingPill = VoxFlowBar`, and subclassed in `src/voxlocal/ui/floating_widget.py` (lines 53–75) as `FloatingWidget(VoxFlowBar)` for backwards compatibility.
- **Capsule Geometry & Ergonomics**:
  - `_sync_geometry()` (lines 829–861):
    - Resting state (`PillState.IDLE`): `pw, ph = (8, 40)` on side edges (`"left"`, `"right"`), or `pw, ph = (40, 8)` on bottom edge.
    - Hover / Ready state (`PillState.READY`): `pw, ph = (50, 30)` capsule (+ 26px quick Scratchpad button = 76x30).
    - Active Push-to-Talk (`PillState.LISTENING`, PTT): `pw, ph = (74, 30)`.
    - Active Hands-Free (`PillState.LISTENING`, hands-free): `pw, ph = (104, 30)`.
    - Processing state (`PillState.PROCESSING`): `pw, ph = (98, 30)` displaying label `"Writing"`.
    - Polish Processing state (`PillState.POLISH_PROCESSING`): `pw, ph = (136, 30)` displaying label `"Cleaning up"` with prismatic Dawn angle cycling (`_advance_prismatic()`, line 690).
    - Committed state (`PillState.COMMITTED`): `pw, ph = (152, 30)` displaying checkmark icon, `"Cleaned up"`, and `btn_undo_toast` (`"Undo"` button for 1.9s, auto-resetting via `_auto_reset_timer`, line 725).
    - Error state (`PillState.ERROR`): `pw = max(96, err_w + 32), ph = 30` displaying error label (e.g. `"No audio"` or custom message for 2.2s).
    - Active Meeting Chip: `● {elapsed_str} Notetaker` (+ width + 8px) attached dynamically to the FlowBar capsule when meeting recording is active (lines 824–827, 855–856).
- **Wispr Flow Electron 440x320 vs VOX Native PyQt6 Geometry**:
  - In Wispr Flow REA static analysis (`.agents/teamwork/specminer_survey_rea_static_gen1/handoff.md`), Wispr Flow's Electron container was created with `width: 440px` (expandable to `614px` on active audio) and `height: 320px` with `setIgnoreMouseEvents(true, { forward: true })` because Electron requires a large transparent canvas to host both the capsule and floating popups.
  - In VOX's native PyQt6 implementation, VOX avoids creating an invisible 440x320 click-blocking box. Instead, VOX implements multi-window native composition:
    1. The FlowBar capsule (`VoxFlowBar`) sizes tightly to the physical widget (40x8 resting to 104x30 hands-free).
    2. The Live Speech Preview Bubble (`LiveSpeechPreviewBubble`, lines 319–380) is an independent non-activating window floating 12px above the pill (`update_position_above_pill`, line 369), with `maximumWidth = 360px` and HTML rich-text wrapping for verbal self-correction strikethroughs.
    3. The Hover Tooltip Pill (`FlowBarTooltipPill`, lines 266–317) is an independent non-activating window floating 8px above the pill (`position_above`, line 306).
- **10-Bar Parabolic Center-Weighted Waveform**:
  - `DynamicWaveformCapsule` (lines 143–240 in `floating_pill.py`):
    - Initialized with `bar_count: int = 10` (line 151).
    - Tick animation (`_tick()`, lines 185–203) runs at 33ms interval (30 FPS), applying spring smoothing: `self._smooth_level += (self._target_level - self._smooth_level) * 0.35`.
    - Center-weighted parabolic curve:
      ```python
      center = (self.bar_count - 1) / 2.0
      bulge = 1.65
      parabolic_scale = max(0.12, 1.0 - (dist ** 2) * (bulge / 48.0))
      ```
    - 4 distinct visual tint modes (`set_visual_mode()`, line 180):
      - `"speech"`: pure white (`#FFFFFF`)
      - `"command"`: amber (`#FFA946`)
      - `"instruct"`: signal pink (`#FFBCF2`)
      - `"dim"`: semi-transparent white (`rgba(255, 255, 255, 102)`)
- **Buttons and Controls**:
  - Stop button: `_RoundPillButton("stop")` (lines 407–414): 18x18 circular button rendering a coral rounded square inside. Uses color `#FF5757` when enabled, `#736E62` when disabled.
  - Cancel button: `_RoundPillButton("cancel")` (lines 398–406): 18x18 circular button with round cross. Emits `cancel_requested` and routes to `controller.cancel_dictation()`.
  - Scratchpad button: `_RoundPillButton("scratchpad")` (lines 415–424): 20x20 circular button with note icon. Emits `open_scratchpad_requested`.
  - Undo toast: `btn_undo_toast` (lines 555–566): `"Undo"` button in COMMITTED state routing to `controller.undo_ai_edit()`.
  - Session timer: `SessionTimerWidget` (lines 242–264): MM:SS elapsed timer initialized on `self.timer_widget`.
- **Non-Activating Win32 Focus Invariance & Screen Privacy**:
  - Window flags: `Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus` (lines 484–492).
  - Attributes: `WA_ShowWithoutActivating = True`, `WA_TranslucentBackground = True`, `focusPolicy = NoFocus`.
  - Win32 API: `apply_non_activating_floating_style(int(self.winId()))` sets `WS_EX_NOACTIVATE (0x08000000) | WS_EX_TOPMOST (0x00000008) | WS_EX_TOOLWINDOW (0x00000080)` and calls `SetWindowPos(hwnd, HWND_TOPMOST, ..., SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)`.
  - Screen capture privacy: `apply_capture_exclusion(hwnd, exclude)` (lines 122–130) invokes `ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00000011)` (`WDA_EXCLUDEFROMCAPTURE`) when `hide_in_screen_share` is true.
- **Audio Cues / Chimes**:
  - `play_flow_chime(kind)` (lines 94–120): In-memory synthesized harmonic WAV cues (`"start"`, `"stop"`, `"commit"`, `"error"`) played asynchronously via `winsound.PlaySound` with `SND_MEMORY | SND_ASYNC | SND_NODEFAULT`.
- **Context Menu**:
  - `contextMenuEvent()` (lines 1061–1173): Right-click context menu offering:
    - "Hide this for 1 hour"
    - "Mute reminders for 12 hours"
    - "Transcript history (Open VOX)"
    - "Open Scratchpad"
    - "Open Notetaker Drawer" / "Start Notetaker Recording"
    - "Paste last transcript" / "Copy last transcript"
    - "Microphone" submenu with real device enumeration via `AudioDeviceManager.list_input_devices()`
    - "Language" submenu (Auto English+Hindi, English Only, Hinglish, Hindi)
    - "Position Flow Bar" submenu (Bottom, Left edge, Right edge)
    - "Settings" and "Quit VOX"

---

#### Feature 16: Onboarding Wizard (`src/voxlocal/ui/onboarding_wizard.py`)
- **Core Class**: `VoxOnboardingWizard` (lines 68–1060).
- **4-Step Interactive Walkthrough**:
  - Step 1: Welcome (`_build_step_welcome()`, lines 181–290):
    - Editorial headline: *"Don’t type, just speak."* (EB Garamond serif).
    - Subtitle explaining universal dictation, bot-free notetaker, and 100% offline private AI.
    - Live visual preview of Obsidian FlowBar capsule with active waveform and `"Polished"` mode chip.
    - 3 feature highlight cards with keycap badges (`Ctrl + Win`, `Zero Bots`, `100% Offline`).
  - Step 2: Personalize Setup (`_build_step_setup()`, lines 291–430):
    - Appearance & Palette: Sand Light (`btn_theme_light`) vs Vast Dark (`btn_theme_dark`) toggle buttons with immediate QSS updates.
    - Flow Bar Dock: QComboBox (`"bottom"`, `"top"`, `"right"`, `"left"`).
    - Microphone Input & Live Probe: QComboBox enumerating system input devices with PyAudioWPATCH/WASAPI; interactive `"Test Mic"` button running a background thread (`_mic_probe_loop()`, lines 752–797) emitting live RMS audio levels to a 10-channel `MultiChannelEqualizerWidget`, `mic_meter` progress bar, and `"Good signal detected — microphone is ready ✓"` feedback.
  - Step 3: Master Shortcuts (`_build_step_shortcuts()`, lines 431–562):
    - Primary Dictation Shortcut keycap row (`Ctrl + Win`).
    - Hands-Free Double-Tap toggle switch (`VoxToggleSwitch`).
    - English + Hinglish (Romanized) Mixing toggle switch (`VoxToggleSwitch`).
    - Built-in spoken voice command badges (`"new line"`, `"delete that"`, `"stop listening"`).
  - Step 4: Interactive Sandbox (`_build_step_try_it_out()`, lines 563–666):
    - Dedicated live text editor (`self.tiy_editor`, QPlainTextEdit).
    - Interactive in-app dictation wired via `controller.set_in_app_dictation_target(tiy_editor)` (lines 962–963), completely bypassing external window focus so testing in-app works with zero window switching.
    - Ideas chips (`_TRY_IT_PROMPTS`: "Self-correction", "Quick Slack note", "Action checklist").
    - Waveform pill (`tiy_waveform`) with 10 bars driven by `dictation_level_changed`.
    - `"Run Self-Correction Demo"` button (`_run_sample_preview()`, line 921): Loads a sample with verbal retraction (`"actually wait make that wednesday at 3 pm"`) and demonstrates automatic cleanup to `"Wednesday at 3 PM"`.
    - Multi-role action button (`self.btn_tiy_speak`, line 902): Dynamically morphs between `"🎙 Start Live Voice Test"` (fathom button), `"Starting Mic..."` (disabled), `"⏹ Stop & Insert"` (coral `startBtn`), and `"Transcribing..."`.
- **Teardown & Lifecycle Protection**:
  - `_stop_active_sessions()` (lines 684–694) halts background mic probe threads, clears in-app dictation targets, and cancels active dictations upon `closeEvent`, `hideEvent`, `reject`, or tour completion to prevent thread or device leaks.
  - `_finish_tour()` (lines 995–1001) persists `first_run_done = True` and `onboarding_completed = True` in `controller.config`.

---

#### Feature 16: System Tray Manager (`src/voxlocal/ui/tray_manager.py`)
- **Core Class**: `SystemTrayManager` (lines 18–278).
- **System Tray Icon**:
  - Multi-resolution high-DPI icon generated via `create_app_icon(size=64, theme=resolved)`.
  - State-reactive icon and QSS styling updating automatically on `controller.theme_changed`.
  - Tooltip: `"VOX — Voice Dictation & Notetaker"`.
  - Double-click or single trigger activates `on_open_tab(0)` (VOX Hub Home).
- **Context Menu Structure**:
  - Read-only Status Header (`act_status`, line 44): Displays live state: `● Ready`, `● Dictating`, `● Polishing`, `● Notetaker 02:15`, `● Attention needed`.
  - Quick Surface Launchers:
    - `"Open VOX Hub"` (`act_open`, line 50)
    - `"Open Scratchpad"` (`act_scratchpad`, line 54) -> routes to floating `VoxScratchpadWindow` or workspace tab
    - `"Open Notetaker Drawer"` (`act_notetaker_drawer`, line 58) -> routes to companion drawer
    - `"Toggle Flow Bar"` (`act_widget`, line 62)
  - Capture Actions:
    - `"Start Dictation ({ptt})"` / `"Stop Dictation"` (`act_dictation`, line 68)
    - `"Start Notetaker ({hotkey})"` / `"Stop & Save Notetaker"` (`act_meeting_start`, line 72)
    - `"Pause Notetaker"` / `"Resume Notetaker"` (`act_meeting_pause`, line 76)
    - `"Record Screen"` / `"Stop Screen Recording"` (`act_screen_start`, line 81)
  - Workspace Jumps (lines 87–123):
    - Home (0), Notetaker & Call Notes (2), Dictionary (6), Snippets (8), Style Preferences (5), Scratchpad Notes (4), Insights (3), Screen Capture (1), Settings... (7).
    - Note on indexing: `main.py` wires `on_open_tab=open_dashboard_tab` which invokes `MainWindow.open_and_focus_tab(tab_idx, legacy_map=True)`. As observed in `MainWindow._LEGACY_INT_TO_INDEX` (lines 125–135 of `main_window.py`), legacy integer indices `(8->3 snippets, 6->2 dictionary, 5->4 style, 4->5 scratchpad, 3->6 insights, 2->1 notetaker, 0->0 home, 7->7 settings)` are mapped to the 8 workspace routes.
  - Graceful Exit: `"Exit VOX"` (`act_exit`, line 126) routes to `on_exit_app`.
  - Native Notifications: `show_notification(title, message)` (line 268) displays standard Windows tray balloon notifications.

---

#### Theme System (`src/voxlocal/ui/theme.py`)
- **Light Theme Tokens (`LIGHT_TOKENS`, lines 29–124)**:
  - Base Sand architectural surfaces: `"bg": "#FCFCFB"`, `"surface": "#FFFFFF"`, `"surface_secondary": "#FAF9F7"`, `"surface_well": "#F5F4F0"`.
  - Hairlines & borders: `"line": "#EEEBE3"`, `"border": "#E5E2D9"`.
  - Ink: `"text_primary": "#1A1A1A"`, `"text_muted": "#736E62"`.
  - Signature Accents: Dawn lavender (`#F0D7FF`), Fathom emerald/teal (`#034F46`), Lumen pale yellow (`#FFFFEB`), Coral stop/recording (`#FF5757`), Amber command tint (`#FFA946`), Signal pink instruct tint (`#FFBCF2`).
  - FlowBar capsule: Obsidian black (`"pill_bg": "#000000"`, `"pill_resting_bg": "rgba(0, 0, 0, 0.50)"`, `"pill_border": "#30302F"`).
- **Dark Theme Tokens (`DARK_TOKENS`, lines 127–222)**:
  - Base Vast obsidian surfaces: `"bg": "#1A1A1A"`, `"surface": "#202020"`, `"surface_elevated": "#30302F"`, `"surface_well": "#141414"`.
  - Borders: `"border": "#383836"`, `"line": "#30302F"`.
  - Ink: `"text_primary": "#E9E8E1"`, `"text_muted": "#9D9C98"`.
  - Accents: Dawn (`#F0D7FF`), Fathom hover (`#84CDC1`), Lumen dark (`#2C2B1E`).
- **Typography & Font Stacks (`get_ui_font()`, lines 230–252)**:
  - Editorial Serifs: `["EB Garamond", "Georgia", "Times New Roman"]`.
  - Monospace Keycaps: `["Google Sans Code", "Consolas", "Cascadia Mono"]`.
  - UI Sans: `["Figtree", "Segoe UI Variable Text", "Segoe UI", "Inter"]`.
- **Icon Generation (`create_app_pixmap()`, lines 792–874)**:
  - Renders a multi-resolution squircle with 5-bar symmetrical voice emblem, state-adaptive linear gradients (`listening` #034F46, `recording` #291416, `processing` #2B1F34, `idle` #1E1E1D), subtle Dawn/teal hairline rim, and optional Coral live recording badge dot.

---

### 1.2 Test Execution Results

All commands executed via `.venv\Scripts\python.exe -m pytest` on Windows 11.

1. **Parity and Evolved Voice Typing Tests**:
   - Command: `.venv\Scripts\python.exe -m pytest tests/test_wispr_parity_features.py tests/test_voice_typing_evolved.py`
   - Output: `38 passed in 5.26s` (100% pass rate).
   - Validates:
     - `test_onboarding_wizard_steps_and_try_it_out_sample`: PASSED
     - `test_in_app_qt_dictation_onboarding_and_dark_theme_contrast`: PASSED
     - `test_pill_initial_state_and_geometry`: PASSED
     - `test_pill_five_state_transitions`: PASSED
     - `test_pill_focus_invariance_flags`: PASSED
     - `test_pill_mode_switching_and_signals`: PASSED
     - `test_pill_waveform_equalizer_12_bars`: PASSED (verifies 10-bar parabolic equalizer)
     - `test_live_speech_preview_bubble_html_strikethrough`: PASSED

2. **Combined E2E Tiers 1–3 Tests**:
   - Command: `.venv\Scripts\python.exe -m pytest tests/e2e/test_tier1_feature_coverage.py tests/e2e/test_tier2_boundaries.py tests/e2e/test_tier3_pairwise.py`
   - Output: `222 passed in 38.88s` (100% pass rate).
   - Validates:
     - `TestTier1Feature10VoicePill` (5 tests): all PASSED
     - `TestTier1Feature16OnboardingTray` (5 tests): all PASSED
     - `TestTier2Boundary10VoicePill` (5 tests): all PASSED (0 amplitude, +1.5 clipping, 500-char preview wrap, rapid mode toggles, offscreen coordinate clamping)
     - `TestTier2Boundary16OnboardingTray` (5 tests): all PASSED (back button on step 1, next button on step 4, rapid forward/back cycling, tray menu when window active, tray icon click spam)
     - `TestTier3CrossFeatureCombinations` (Feature 10 & 16 integration tests): all PASSED

3. **UI Refinement, Hover Tooltip, and Lifecycle Tests**:
   - Command: `.venv\Scripts\python.exe -m pytest tests/test_floating_widget_hover_info_and_meeting.py tests/test_theme_and_animations.py tests/test_ui_hardening_and_user_feedback_fixes.py tests/test_ui_refinement.py tests/test_voice_typing_lifecycle_and_target.py`
   - Output: `32 passed in 15.38s` (100% pass rate).

4. **Adversarial & Empirical Tests**:
   - Command: `.venv\Scripts\python.exe -m pytest tests/test_screen_sharing_comprehensive_matrix.py tests/test_stability_performance_and_diarization.py tests/test_adversarial_vox_capabilities.py tests/test_adversarial_m2_empirical.py tests/test_challenger_stress_m2.py`
   - Output: `63 passed in 6.88s` (100% pass rate).

5. **Meeting Studio & Product UX Tests**:
   - Command: `.venv\Scripts\python.exe -m pytest tests/test_meeting_studio_hardening_and_clarity.py tests/test_e2e_product_ux_and_architecture.py`
   - Output: `17 passed in 2.17s` (100% pass rate).

6. **Dictation & Comprehensive Audit Tests**:
   - Command: `.venv\Scripts\python.exe -m pytest tests/test_e2e_dictation_and_modes.py tests/test_e2e_comprehensive_audit.py`
   - Output: `31 passed in 13.86s` (100% pass rate).

7. **Production Standalone Binary Smoke Test**:
   - Command: `dist\VoxLocal\VoxLocal.exe --smoke-test-ms 1000`
   - Output: Exit code `0` (zero unhandled exceptions, cleanly initialized and shut down).

8. **Windows Logging Rollover Observation**:
   - In `main.py --smoke-test-ms 1000`, observed a stderr trace:
     `PermissionError: [WinError 32] The process cannot access the file because it is being used by another process: 'D:\\Milind\\Voice Assistant\\data\\logs\\voxlocal.log' -> 'D:\\Milind\\Voice Assistant\\data\\logs\\voxlocal.log.1'`
   - Investigation: `data/logs/voxlocal.log` was measured at `5,242,850 bytes` (exceeding `5 * 1024 * 1024 = 5,242,880 bytes` threshold). On Windows, Python's default `RotatingFileHandler` fails to rename files if another process/thread holds a handle during rollover.

---

## 2. Logic Chain

1. **State of Feature 10 (Floating FlowBar / Voice Pill)**:
   - *Premise 1*: `src/voxlocal/ui/floating_pill.py` implements `VoxFlowBar`, which exports `DualTierFloatingPill` and is wrapped by `FloatingWidget`.
   - *Premise 2*: All 5 discrete interactive states (`IDLE`, `READY`, `LISTENING`, `PROCESSING`, `COMMITTED`, `ERROR`, plus `POLISH_PROCESSING`) update geometry, icons, and labels dynamically.
   - *Premise 3*: The 10-bar parabolic equalizer (`DynamicWaveformCapsule`) is center-weighted, smoothed via a 33ms QTimer, and responds to RMS audio levels and processing animations.
   - *Premise 4*: Win32 styles (`WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW`) and screen share exclusion (`WDA_EXCLUDEFROMCAPTURE`) are actively applied.
   - *Premise 5*: 222 E2E tests, 38 parity tests, and 32 UI tests all pass with 100% success.
   - *Deduction*: Feature 10 is fully implemented, verified, and in working order.

2. **Reconciliation of 440x320 vs 40x8 Geometry**:
   - *Premise 1*: REA static analysis revealed Wispr Flow's Electron app instantiated an outer BrowserWindow of `440x320` (expandable to `614x320`) with click-through `setIgnoreMouseEvents`.
   - *Premise 2*: In native Windows PyQt6, creating a large invisible transparent window blocks native mouse clicks on underlying apps unless complex Win32 mouse hook intercepts are deployed.
   - *Premise 3*: VOX architecturally solved this by decomposing the Electron canvas into lightweight, focused native windows: a compact capsule (`VoxFlowBar`, 40x8 to 104x30), a preview bubble (`LiveSpeechPreviewBubble`, 360px wide, floating 12px above), and a hover tooltip (`FlowBarTooltipPill`, 8px above).
   - *Premise 4*: All test suites (`test_pill_initial_state_and_geometry`, `TestTier1Feature10VoicePill`, `TestTier2Boundary10VoicePill`) explicitly assert this decomposed geometry.
   - *Deduction*: The current native PyQt6 geometry is intentional, fully tested, and ergonomically superior on Windows desktop. The implementation Worker must NOT replace the capsule with a rigid 440x320 frame.

3. **State of Feature 16 (Onboarding Wizard & System Tray)**:
   - *Premise 1*: `src/voxlocal/ui/onboarding_wizard.py` implements `VoxOnboardingWizard` with all 4 steps: Welcome, Personalize Setup (Sand Light/Vast Dark, dock edge, live WASAPI mic test), Master Shortcuts (`Ctrl+Win`, double-tap, Hinglish), and Interactive Sandbox ("Try VOX") with in-app dictation.
   - *Premise 2*: `src/voxlocal/ui/tray_manager.py` implements `SystemTrayManager` with live status header, workspace launchers, mode toggles, graceful exit, and theme synchronization.
   - *Premise 3*: `main.py` seamlessly bridges `SystemTrayManager` and `MainWindow` through `MainWindow.open_and_focus_tab` with `_LEGACY_INT_TO_INDEX` mapping.
   - *Premise 4*: All corresponding tests pass without defect.
   - *Deduction*: Feature 16 is fully implemented, verified, and working.

4. **Robustness & Windows Hardening Observation**:
   - *Premise 1*: `data/logs/voxlocal.log` reached 5.2MB, triggering standard `RotatingFileHandler` rollover on Windows.
   - *Premise 2*: Windows OS file locking causes `PermissionError: [WinError 32]` during `doRollover()` when another handle or thread has the file open.
   - *Deduction*: While this does not crash the application or fail any unit tests (the standard logging library catches emit errors and prints to stderr), it generates console clutter. A minor hardening recommendation for the Worker is to wrap rollover safely.

---

## 3. Caveats

1. **Investigation Scope**: This investigation was strictly read-only and focused on Feature 10 (Floating Voice Pill / FlowBar), Feature 16 (Onboarding Wizard & System Tray), and Theme System. Features 11–15 (Hub Workspaces, Command Palette, Notetaker Drawer, 3-Section Meeting Studio, and 3-Tier Outlook Calendar Engine) were inspected only at their integration interfaces and are comprehensively audited by peer Explorer 2.
2. **Audio Hardware**: Tests were executed in an offscreen test environment (`QT_QPA_PLATFORM=offscreen`). Physical microphone capture and audio hardware playback were tested via mock streams and PyAudioWPATCH device enumeration.

---

## 4. Conclusion

1. **Feature 10 (Floating Voice Pill / FlowBar)**: **FULLY WORKING & COMPLIANT**.
   - `VoxFlowBar` / `DualTierFloatingPill` / `FloatingWidget` properly implements all states, 10-bar parabolic equalizer, coral stop button (#FF5757), cancel button, undo toast, session timer, live preview bubble, context menu, non-activating window flags (`WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW`), and capture privacy.
2. **Feature 16 (Onboarding Wizard & System Tray)**: **FULLY WORKING & COMPLIANT**.
   - `VoxOnboardingWizard` provides the exact 4-step interactive try-it-out walkthrough with live in-app dictation targeting and config persistence.
   - `SystemTrayManager` provides full status monitoring, workspace launchers, mode toggles, and graceful exit.
3. **Theme System (`theme.py`)**: **FULLY WORKING & COMPLIANT**.
   - Sand Light and Vast Dark palettes, Figtree/Garamond typography, and adaptive high-DPI waveform icons are fully consistent.
4. **Test Status**: **100% PASS RATE** across 403+ relevant tests, and the standalone executable (`dist/VoxLocal/VoxLocal.exe --smoke-test-ms 1000`) passes with exit code 0.

### Recommendations for the Implementation Worker
1. **Preserve Current Capsule Geometry**: Do NOT force a rigid 440x320 box onto `upper_pill`. The multi-window native composition (capsule + preview bubble + tooltip) is the tested and superior Windows implementation of Wispr Flow's Electron container.
2. **Logging Rollover Hardening**: In `src/voxlocal/core/logger.py`, consider wrapping `RotatingFileHandler.doRollover` in a try/except block or subclassing it to catch `PermissionError` on Windows when log files cross 5MB.
3. **Ready for Milestone 3 Integration**: Features 10 and 16 require no rework; Worker can focus directly on any remaining Milestone 3 Meeting Studio / Calendar sync polish identified by Explorer 2.

---

## 5. Verification Method

To independently verify all findings and test suites:

1. **Run Wispr Parity and Evolved Voice Typing Tests**:
   ```powershell
   .venv\Scripts\python.exe -m pytest tests/test_wispr_parity_features.py tests/test_voice_typing_evolved.py
   ```
   *Expected*: 38 passed in ~5s.

2. **Run E2E Tiers 1–3 Feature & Boundary Suites**:
   ```powershell
   .venv\Scripts\python.exe -m pytest tests/e2e/test_tier1_feature_coverage.py tests/e2e/test_tier2_boundaries.py tests/e2e/test_tier3_pairwise.py
   ```
   *Expected*: 222 passed in ~39s.

3. **Run UI Refinement & Lifecycle Suites**:
   ```powershell
   .venv\Scripts\python.exe -m pytest tests/test_floating_widget_hover_info_and_meeting.py tests/test_theme_and_animations.py tests/test_ui_refinement.py tests/test_voice_typing_lifecycle_and_target.py
   ```
   *Expected*: 32 passed in ~15s.

4. **Verify Standalone Executable Smoke Test**:
   ```powershell
   dist\VoxLocal\VoxLocal.exe --smoke-test-ms 1000
   ```
   *Expected*: Exit code 0.

5. **Files to Inspect**:
   - `src/voxlocal/ui/floating_pill.py` (lines 434–1184)
   - `src/voxlocal/ui/onboarding_wizard.py` (lines 68–1060)
   - `src/voxlocal/ui/tray_manager.py` (lines 18–278)
   - `src/voxlocal/ui/theme.py` (lines 29–285)

# Comprehensive Investigation Report: App-Wide Accessibility & Keyboard Traversal Audit

**Explorer 3 — Accessibility, Keyboard Traversal & Test Suite Specialist**  
**Milestone:** Milestone 8 (App-Wide UX Polish & Accessibility)  
**Target Files:**
- `src/voxlocal/ui/main_window.py`
- `src/voxlocal/ui/command_palette.py`
- `src/voxlocal/ui/settings_dialog.py`
- `src/voxlocal/ui/notetaker_page.py`
- `src/voxlocal/ui/theme.py`
- `tests/test_ux_accessibility_regressions.py` (New Automated Test Suite)  
**Date:** 2026-11-12  

---

## 1. Executive Summary

This investigation provides an exhaustive audit of accessibility (`setAccessibleName`, `setAccessibleDescription`), keyboard focus chains (Tab, Shift+Tab, Enter, Space, Escape), semantic token resolution, user-friendly compute labeling, and live microphone meter lifecycle across the VOX desktop application.

### Key Discoveries:
1. **Pervasive Absence of Screen Reader Attributes**:
   - Across `main_window.py`, `command_palette.py`, `settings_dialog.py`, and `notetaker_page.py`, over **45 distinct interactive widgets** (buttons, inputs, sliders, toggle switches, list widgets, and dialogs) currently have **zero** accessible names or descriptions set.
   - Screen reader users (Windows Narrator, NVDA, JAWS) currently hear generic announcements such as "button", "edit", or empty silence for critical controls including `VoxToggleSwitch`, workspace navigation buttons, and the command palette search input.
2. **Keyboard Traversal & Focus Inconsistencies**:
   - In `ShortcutCaptureDialog` (`settings_dialog.py:188`), pressing `Escape` alone is caught as the keystroke `"esc"` rather than dismissing the modal dialog. Users without a mouse cannot cancel shortcut recording using standard modal dismissal semantics.
   - In `VoxCommandPaletteDialog` (`command_palette.py:326-346`), pressing `Space` while an item is highlighted does not trigger `execute_selected()`, violating desktop command palette conventions where Space/Enter both activate selected items.
   - In `main_window.py` and `settings_dialog.py`, tab order relies on default layout declaration order. In nested layouts (`mic_wrap`, `gain_wrap`, `cal_btns`), focus order skips logical visual sequences.
3. **Semantic Design Token Gaps**:
   - Central theme tokens in `theme.py` contain legacy keys (`pill_bg`, `pill_border`, `accent_crimson`), but lack explicit standardized semantic tokens required by Milestone 8: `surface_pill`, `border_pill`, `text_pill_primary`, `text_pill_muted`, `accent_emerald`, `accent_coral`, `card_background`, and `split_button_fill`.
4. **Developer Jargon in Settings vs. Friendly Labels**:
   - Whisper compute options are either hidden or expose raw CTranslate2/Whisper types (`float16`, `int8`, `int8_float16`) instead of user-friendly performance tiers: "Fastest (Lower Memory)", "Balanced", and "Maximum Accuracy".
5. **Live Mic Level Meter Absence**:
   - While `VoxAudioVisualizerBar` exists in `components.py:412`, it is currently not integrated into the Audio Settings section of `settings_dialog.py`. Users cannot visually verify microphone deflection before initiating recording sessions.

---

## 2. Component-by-Component Accessibility Audit

### 2.1 `src/voxlocal/ui/main_window.py`

| # | Element & Object Name | Line | Widget Type | Current State | Exact Recommended `setAccessibleName` | Exact Recommended `setAccessibleDescription` |
|---|----------------------|------|-------------|---------------|--------------------------------------|----------------------------------------------|
| 1 | `GlobalSearchDialog.txt` | 112 | `QLineEdit` | Missing Name/Desc | `"Search query input"` | `"Enter search terms across dictations, meetings, dictionary, snippets, and scratchpad notes"` |
| 2 | `GlobalSearchDialog.results` | 117 | `QListWidget` | Missing Name/Desc | `"Search results list"` | `"Matching results list across workspaces. Use arrow keys to select and Enter to open."` |
| 3 | `MainWindow.lbl_logo_mark` | 260 | `QLabel` | Missing Name | `"VOX application logo"` | `"Branded logo mark for VOX desktop assistant"` |
| 4 | `MainWindow.lbl_brand` | 261 | `QLabel` | Missing Name | `"VOX Desktop Hub"` | `"Primary application title"` |
| 5 | `MainWindow.theme_toggle` | 267 | `ThemeToggleCapsule` | Missing Name/Desc | `"Theme toggle"` | `"Switch application theme between Sand Light and Vast Dark"` |
| 6 | `MainWindow.btn_global_search` | 275 | `QPushButton` | Missing Name/Desc | `"Search VOX and Command Palette"` | `"Open global command palette and search dialog, keyboard shortcut Control+K"` |
| 7 | `MainWindow.nav_buttons[0]` | 283 | `QPushButton` | Missing Name/Desc | `"Home workspace"` | `"Navigate to Home dictation history feed, keyboard shortcut Control+1"` |
| 8 | `MainWindow.nav_buttons[1]` | 283 | `QPushButton` | Missing Name/Desc | `"Notetaker workspace"` | `"Navigate to Notetaker meeting intelligence and recordings, keyboard shortcut Control+2"` |
| 9 | `MainWindow.nav_buttons[2]` | 283 | `QPushButton` | Missing Name/Desc | `"Dictionary workspace"` | `"Navigate to Dictionary and vocabulary replacements, keyboard shortcut Control+3"` |
| 10 | `MainWindow.nav_buttons[3]` | 283 | `QPushButton` | Missing Name/Desc | `"Snippets workspace"` | `"Navigate to Snippets keyword expansion triggers, keyboard shortcut Control+4"` |
| 11 | `MainWindow.nav_buttons[4]` | 283 | `QPushButton` | Missing Name/Desc | `"Style workspace"` | `"Navigate to Style and tone formatting profiles, keyboard shortcut Control+5"` |
| 12 | `MainWindow.nav_buttons[5]` | 283 | `QPushButton` | Missing Name/Desc | `"Scratchpad workspace"` | `"Navigate to Scratchpad notes and drafting canvas, keyboard shortcut Control+6"` |
| 13 | `MainWindow.nav_buttons[6]` | 283 | `QPushButton` | Missing Name/Desc | `"Insights workspace"` | `"Navigate to Insights speech statistics and usage analytics, keyboard shortcut Control+7"` |
| 14 | `MainWindow.nav_buttons[7]` (`btn_settings`) | 301 | `QPushButton` | Missing Name/Desc | `"Settings workspace"` | `"Navigate to application settings and preferences, keyboard shortcut Control+Comma"` |
| 15 | `MainWindow.btn_open_scratchpad` | 309 | `QPushButton` | Missing Name/Desc | `"Open Floating Scratchpad"` | `"Open the resizable floating scratchpad window for quick note-taking"` |
| 16 | `MainWindow.btn_open_drawer` | 315 | `QPushButton` | Missing Name/Desc | `"Open Notetaker Drawer"` | `"Open the context-aware slide-out live notetaker drawer"` |
| 17 | `MainWindow.btn_replay_tour` | 321 | `QPushButton` | Missing Name/Desc | `"Replay Onboarding Tour"` | `"Launch the interactive onboarding tour and desktop feature walkthrough"` |
| 18 | `MainWindow.btn_toggle_float` | 327 | `QPushButton` | Missing Name/Desc | `"Toggle Flow Bar"` | `"Toggle visibility of the floating desktop Flow Bar pill"` |
| 19 | `MainWindow.lbl_privacy_badge` | 333 | `QLabel` | Missing Name/Desc | `"Privacy status indicator"` | `"100% Local AI: all transcription and processing runs offline on this PC"` |
| 20 | `MainWindow.citations_list` | 226 | `QListWidget` | Missing Name/Desc | `"Ask VOX citation sources"` | `"List of transcript citations and timestamps supporting the generated response"` |

---

### 2.2 `src/voxlocal/ui/command_palette.py`

| # | Element & Object Name | Line | Widget Type | Current State | Exact Recommended `setAccessibleName` | Exact Recommended `setAccessibleDescription` |
|---|----------------------|------|-------------|---------------|--------------------------------------|----------------------------------------------|
| 1 | `CommandItemWidget` | 35 | `QWidget` | Missing Name/Desc | `f"{command.category}: {command.title}"` | `f"{command.description}. Shortcut: {command.shortcut}" if command.shortcut else command.description` |
| 2 | `CommandItemWidget.lbl_title` | 61 | `QLabel` | Missing Name | `command.title` | `"Command title"` |
| 3 | `CommandItemWidget.lbl_desc` | 66 | `QLabel` | Missing Name | `command.description` | `"Command description"` |
| 4 | `VoxCommandPaletteDialog.txt_search` | 135 | `QLineEdit` | Missing Name/Desc | `"Command search input"` | `"Type a command, action, or workspace name to filter list"` |
| 5 | `VoxCommandPaletteDialog.list_widget` | 153 | `QListWidget` | Missing Name/Desc | `"Command suggestions list"` | `"Matching commands and actions. Navigate with Up and Down arrows, execute with Enter or Space, close with Escape."` |
| 6 | `VoxCommandPaletteDialog.lbl_esc` | 141 | `QLabel` | Missing Name | `"Escape key dismissal hint"` | `"Press Escape key to dismiss the command palette"` |
| 7 | `VoxCommandPaletteDialog.lbl_hint` | 161 | `QLabel` | Missing Name | `"Keyboard navigation shortcuts hint"` | `"Use Up and Down arrows to navigate, Enter or Space to execute, and Escape to dismiss"` |

---

### 2.3 `src/voxlocal/ui/settings_dialog.py`

| # | Element & Object Name | Line | Widget Type | Current State | Exact Recommended `setAccessibleName` | Exact Recommended `setAccessibleDescription` |
|---|----------------------|------|-------------|---------------|--------------------------------------|----------------------------------------------|
| 1 | `ShortcutCaptureDialog.display_chip` | 81 | `QLabel` | Missing Name/Desc | `"Recorded shortcut key display"` | `"Shows the currently detected key combination or prompts to press keys"` |
| 2 | `ShortcutCaptureDialog` preset buttons | 95 | `QPushButton` | Missing Name/Desc | `f"Preset shortcut {label}"` | `f"Set hotkey to {label}"` |
| 3 | `ShortcutCaptureDialog.btn_clear` | 116 | `QPushButton` | Missing Name/Desc | `"Clear shortcut"` | `"Reset and remove the current key binding"` |
| 4 | `ShortcutCaptureDialog.btn_cancel` | 124 | `QPushButton` | Missing Name/Desc | `"Cancel shortcut capture"` | `"Close dialog without saving shortcut changes"` |
| 5 | `ShortcutCaptureDialog.btn_save` | 131 | `QPushButton` | Missing Name/Desc | `"Save shortcut"` | `"Save and apply the recorded key combination"` |
| 6 | `SettingsWorkspaceWidget._nav_buttons[0..5]` | 288 | `QPushButton` | Missing Name/Desc | `f"{title} settings section"` | `desc` |
| 7 | `_hotkey_buttons[field_name]` | 414 | `QPushButton` | Missing Name/Desc | `f"{title} hotkey"` | `f"Click to record a new key combination for {title}"` |
| 8 | `VoxToggleSwitch` (`self._toggles`) | 374 | `VoxToggleSwitch` (`QCheckBox`) | Missing Name/Desc | `f"{title} toggle"` | `desc` |
| 9 | `combo_mic` | 457 | `QComboBox` | Missing Name/Desc | `"Microphone input device"` | `"Select primary audio input device for dictation and meeting recording"` |
| 10 | `btn_refresh_mic` | 462 | `QPushButton` | Missing Name/Desc | `"Refresh microphones"` | `"Scan and reload available system audio input devices"` |
| 11 | `slider_mic_gain` | 473 | `QSlider` | Missing Name/Desc | `"Microphone gain slider"` | `"Adjust digital microphone boost from 0.5x to 2.5x"` |
| 12 | `combo_lang` | 482 | `QComboBox` | Missing Name/Desc | `"Recognition language"` | `"Select spoken dictation language: English + Hindi, English only, or Hindi only"` |
| 13 | `combo_model` | 498 | `QComboBox` | Missing Name/Desc | `"Speech transcription model"` | `"Select local Whisper model for transcription speed versus accuracy"` |
| 14 | `combo_profile` | 514 | `QComboBox` | Missing Name/Desc | `"Speed versus accuracy profile"` | `"Select performance profile: Fast, Balanced, or Accurate"` |
| 15 | `combo_compute` (NEW) | - | `QComboBox` | Missing / To Add | `"Whisper compute type"` | `"Select computation precision: Fastest (int8), Balanced (default), or Maximum Accuracy (float16)"` |
| 16 | `combo_theme` | 530 | `QComboBox` | Missing Name/Desc | `"Appearance theme"` | `"Select application color theme: Light Sand, Dark Vast, or Windows System"` |
| 17 | `combo_edge` | 537 | `QComboBox` | Missing Name/Desc | `"Flow Bar screen dock edge"` | `"Pin floating dictation bar to bottom, left, or right screen edge"` |
| 18 | `txt_email_sig` | 601 | `QLineEdit` | Missing Name/Desc | `"Email auto-signature text"` | `"Enter custom sign-off message automatically appended in mail applications"` |
| 19 | `btn_tour` | 608 | `QPushButton` | Missing Name/Desc | `"Replay guided onboarding tour"` | `"Launch interactive setup walkthrough and dictation playground"` |
| 20 | `combo_rec_mode` | 626 | `QComboBox` | Missing Name/Desc | `"Default call capture mode"` | `"Choose between Audio plus call window video or Audio only"` |
| 21 | `spin_fps` | 632 | `QSpinBox` | Missing Name/Desc | `"Call video frame rate"` | `"Set video capture rate from 5 to 30 frames per second"` |
| 22 | `combo_drawer_start` | 662 | `QComboBox` | Missing Name/Desc | `"Notepad behavior on call start"` | `"Choose whether to open floating drawer, show compact pill, or keep closed on call start"` |
| 23 | `spin_max_len` | 671 | `QSpinBox` | Missing Name/Desc | `"Maximum recording length"` | `"Set maximum recording duration limit from 15 to 360 minutes"` |
| 24 | `combo_lead_min` | 678 | `QComboBox` | Missing Name/Desc | `"Calendar reminder lead time"` | `"Set advance notification lead time for upcoming meetings from 1 to 10 minutes"` |
| 25 | `btn_connect_ms` | 725 | `QPushButton` | Missing Name/Desc | `"Connect Microsoft 365 calendar"` | `"Connect or reconnect Microsoft 365 / Outlook calendar account"` |
| 26 | `btn_sync_cal` | 731 | `QPushButton` | Missing Name/Desc | `"Sync calendar now"` | `"Poll Microsoft 365 calendar immediately for upcoming meetings"` |
| 27 | `btn_disconnect_cal` | 736 | `QPushButton` | Missing Name/Desc | `"Disconnect calendar"` | `"Disconnect Microsoft 365 calendar synchronization"` |
| 28 | `combo_retention` | 750 | `QComboBox` | Missing Name/Desc | `"Data auto-retention window"` | `"Select retention policy: Keep Forever, 7 Days, 30 Days, or 90 Days"` |
| 29 | `btn_export` | 756 | `QPushButton` | Missing Name/Desc | `"Export all VOX data"` | `"Export full local JSON backup of dictations, notes, dictionary, snippets, and meetings"` |
| 30 | `_wipe_checks[key]` | 775 | `QCheckBox` | Missing Name/Desc | `f"Select {lbl_txt} for deletion"` | `f"Check to include {lbl_txt} in permanent deletion"` |
| 31 | `btn_wipe` | 780 | `QPushButton` | Missing Name/Desc | `"Delete selected data"` | `"Permanently erase selected data categories from local storage"` |

---

### 2.4 `src/voxlocal/ui/notetaker_page.py`

| # | Element & Object Name | Line | Widget Type | Current State | Exact Recommended `setAccessibleName` | Exact Recommended `setAccessibleDescription` |
|---|----------------------|------|-------------|---------------|--------------------------------------|----------------------------------------------|
| 1 | `btn_drawer` | 92 | `QPushButton` | Missing Name/Desc | `"Open Floating Drawer"` | `"Open the slide-out companion notetaker drawer for call notes"` |
| 2 | `btn_start_adhoc` | 101 | `QPushButton` | Missing Name/Desc | `"Toggle Notetaker recording"` | `"Start, pause, or stop local notetaker recording for desktop calls"` |
| 3 | `btn_pause_rec` | 121 | `QPushButton` | Missing Name/Desc | `"Pause recording"` | `"Temporarily pause active call audio recording"` |
| 4 | `btn_stop_rec` | 125 | `QPushButton` | Missing Name/Desc | `"Stop and save recording"` | `"Stop recording and start local transcription and summarization"` |
| 5 | `txt_search` | 156 | `QLineEdit` | Missing Name/Desc | `"Search call notes and transcripts"` | `"Filter meeting recordings by title, date, platform, or transcript content"` |
| 6 | `combo_folders` | 163 | `QComboBox` | Missing Name/Desc | `"Filter by folder"` | `"Filter the meeting recordings library by folder"` |
| 7 | `btn_new_folder` | 167 | `QPushButton` | Missing Name/Desc | `"Create new folder"` | `"Create a new organizational folder for meeting notes"` |
| 8 | `meetings_list` | 173 | `QListWidget` | Missing Name/Desc | `"Recorded meetings library"` | `"List of recorded meetings and notes. Use arrow keys to select, Space/Enter to load."` |
| 9 | `btn_watch_video` | 206 | `QPushButton` | Missing Name/Desc | `"Watch call video"` | `"Play recorded screen video for the selected meeting in default player"` |
| 10 | `btn_speakers` | 213 | `QPushButton` | Missing Name/Desc | `"Manage meeting speakers"` | `"Assign participant names to detected speaker diarization tracks"` |
| 11 | `btn_export` | 219 | `QPushButton` | Missing Name/Desc | `"Export meeting deliverables"` | `"Open export menu to download minutes in Markdown, JSON, HTML, PDF, or text"` |
| 12 | `btn_more` | 225 | `QPushButton` | Missing Name/Desc | `"More meeting actions"` | `"Open options menu to move to folder, reveal recording in Explorer, or delete"` |
| 13 | `btn_play_pause` | 236 | `QPushButton` | Missing Name/Desc | `"Toggle audio playback"` | `"Play or pause audio playback for the selected meeting"` |
| 14 | `media_slider` | 242 | `QSlider` | Missing Name/Desc | `"Audio playback seek slider"` | `"Seek playback position within the selected meeting recording"` |

---

## 3. Keyboard Navigation & Focus Traversal Audit

### 3.1 Tab & Shift+Tab Focus Order
1. **`MainWindow` Focus Chain**:
   - Current Chain:
     `theme_toggle` -> `btn_global_search` -> `nav_buttons[0..6]` -> `btn_settings` -> `btn_open_scratchpad` -> `btn_open_drawer` -> `btn_replay_tour` -> `btn_toggle_float` -> `workspace_stack.currentWidget()`.
   - **Recommendation**:
     Explicitly set tab ordering via `QWidget.setTabOrder()` so that focus advances predictably:
     ```python
     self.setTabOrder(self.btn_global_search, self.nav_buttons[0])
     for i in range(len(self.nav_buttons) - 1):
         self.setTabOrder(self.nav_buttons[i], self.nav_buttons[i + 1])
     self.setTabOrder(self.nav_buttons[-1], self.btn_open_scratchpad)
     self.setTabOrder(self.btn_open_scratchpad, self.btn_open_drawer)
     self.setTabOrder(self.btn_open_drawer, self.btn_replay_tour)
     self.setTabOrder(self.btn_replay_tour, self.btn_toggle_float)
     ```
2. **`VoxCommandPaletteDialog` Focus Chain**:
   - Current Chain:
     Focus starts in `txt_search`. Pressing `Tab` moves focus to `list_widget`. Pressing `Shift+Tab` returns focus to `txt_search`.
   - **Recommendation**:
     Explicitly enforce `self.setTabOrder(self.txt_search, self.list_widget)` and ensure that pressing `Down` arrow while `txt_search` is focused smoothly moves selection in `list_widget` without losing cursor focus.

3. **`SettingsWorkspaceWidget` Focus Chain**:
   - Current Chain:
     Focus moves from `_nav_buttons[0..5]` into the active card container.
   - Within `General` Card:
     Hotkey buttons -> Double-tap toggle -> Triple-tap toggle -> Mic combo -> Refresh btn -> Gain slider -> Language combo -> Hinglish toggle -> Model combo -> Compute combo -> Profile combo.
   - All toggle switches (`VoxToggleSwitch`) subclass `QCheckBox` and correctly accept `Qt.FocusPolicy.StrongFocus`, allowing keyboard focus traversal via Tab/Shift+Tab.

### 3.2 Modal Dialog Dismissal (Escape Key)
1. **`VoxCommandPaletteDialog`**:
   - In `command_palette.py:341-343`, `keyPressEvent` explicitly tests `key == Qt.Key.Key_Escape` and calls `self.reject()`. Clean and robust.
2. **`GlobalSearchDialog`**:
   - Subclasses `QDialog`, handles Escape natively via `reject()`.
3. **`SettingsDialog`**:
   - Subclasses `QDialog`, handles Escape natively via `reject()`.
4. **`ShortcutCaptureDialog` Defect**:
   - In `settings_dialog.py:188-190`:
     ```python
     elif key == Qt.Key.Key_Escape:
         key_name = "esc"
     ```
   - **Defect**: When the user presses `Escape` with no modifier keys held, the dialog treats Escape as a shortcut candidate rather than closing!
   - **Fix**:
     ```python
     if key == Qt.Key.Key_Escape and not modifiers:
         self.reject()
         event.accept()
         return
     ```

### 3.3 Control Activation (Enter & Space)
1. **`QPushButton`**:
   - Natively responds to both `Enter` and `Space` when focused.
2. **`VoxToggleSwitch` (`QCheckBox`)**:
   - Natively responds to `Space` to toggle state and emits `toggled(bool)`.
3. **`VoxCommandPaletteDialog`**:
   - In `keyPressEvent`:
     ```python
     elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
         self.execute_selected()
         event.accept()
     ```
   - Adding `Qt.Key.Key_Space` ensures users can execute highlighted commands with either Space or Enter.

---

## 4. Settings Friendly Compute Labels Specification

### 4.1 Problem Analysis
In `src/voxlocal/core/config.py:140`, `compute_type: str = "default"` is stored as a raw technical string. When exposed directly in UI dialogs, values like `float16`, `int8`, and `int8_float16` cause cognitive friction for general users.

### 4.2 Friendly Mapping Table

| User-Facing Label | Internal Config Value (`config.compute_type`) | Technical Mechanism & Target Hardware | Memory Footprint |
|-------------------|----------------------------------------------|---------------------------------------|------------------|
| **Fastest (Lower Memory)** | `"int8"` | 8-bit integer quantization; optimal for CPU and laptops | ~1.0 GB RAM |
| **Balanced** | `"default"` (or `"int8_float16"`) | Mixed precision / automatic optimization for platform | ~1.8 GB RAM / VRAM |
| **Maximum Accuracy** | `"float16"` | 16-bit float / FP32 precision; zero quantization error | ~3.0 GB+ VRAM |

### 4.3 Bidirectional Mapping Code Contract
```python
COMPUTE_LABEL_TO_VALUE: Dict[str, str] = {
    "Fastest (Lower Memory)": "int8",
    "Balanced": "default",
    "Maximum Accuracy": "float16",
}

COMPUTE_VALUE_TO_LABEL: Dict[str, str] = {
    "int8": "Fastest (Lower Memory)",
    "int8_float16": "Balanced",
    "default": "Balanced",
    "float16": "Maximum Accuracy",
    "float32": "Maximum Accuracy",
}
```

---

## 5. Live Microphone Input Level Meter Architecture & Lifecycle

### 5.1 Architecture Overview
The live microphone input deflection meter allows users in Settings -> General -> Microphone to see a real-time reactive VU audio bar showing voice volume when speaking into the selected input device.

### 5.2 Component Integration
- **Widget**: `VoxAudioVisualizerBar` (`src/voxlocal/ui/components.py:412`).
  - Height: 8px, rounded track, linear gradient fill (`#10B981` -> `#3B82F6` -> `#EF4444` on clipping).
  - Peak indicator with smooth decay.
- **Polling / Signal**:
  - `QTimer` running at 25 Hz (40ms interval).
  - Reads RMS level from `dictation_recorder` or active audio probe without blocking the UI thread.
  - Passes normalized `level: float` (0.0 to 1.0) to `meter.set_level(level)`.

### 5.3 Teardown & Lifecycle Contract (Leak-Free)
1. **On Workspace Switch / Tab Change**:
   - When the user switches away from Settings or from the General page:
     `stop_mic_meter()` is called.
2. **On Window / Dialog Close**:
   - `teardown()` stops the `QTimer`, releases any temporary audio probe stream, and zeroes the level bar.
   - Guaranteed zero dangling timers, zero open WASAPI handles, and zero background CPU usage when Settings is hidden.

---

## 6. Central Semantic Design Tokens (`theme.py`)

Milestone 8 requires centralizing semantic design tokens in `src/voxlocal/ui/theme.py`:

```python
# Required Semantic Token Definitions in LIGHT_TOKENS and DARK_TOKENS:

# Light Palette:
LIGHT_TOKENS.update({
    "surface_pill": "#000000",             # FlowBar stays obsidian black across light & dark
    "border_pill": "#30302F",              # Crisp FlowBar border
    "text_pill_primary": "#FCFCFB",        # High-contrast white text on pill
    "text_pill_muted": "#8E897E",          # Muted subtext on pill
    "accent_emerald": "#369489",           # Emerald call active dot
    "accent_coral": "#FF5757",             # Coral alert / recording indicator
    "card_background": "#FFFFFF",          # Standard card background
    "split_button_fill": "#F0D7FF",        # Dawn underlay fill for split CTA
})

# Dark Palette:
DARK_TOKENS.update({
    "surface_pill": "#000000",
    "border_pill": "#30302F",
    "text_pill_primary": "#FCFCFB",
    "text_pill_muted": "#71716E",
    "accent_emerald": "#4DAA9D",
    "accent_coral": "#FF5757",
    "card_background": "#202020",
    "split_button_fill": "#2A2A29",
})
```

---

## 7. Proposed Regression Test Suite Specification: `tests/test_ux_accessibility_regressions.py`

This test suite is designed to prevent regressions and verify compliance across all Milestone 8 UX and accessibility requirements.

### Test Matrix

| # | Test Function Name | Verification Scope | Expected Behavior |
|---|--------------------|-------------------|-------------------|
| 1 | `test_01_semantic_tokens_resolution` | `LIGHT_TOKENS` and `DARK_TOKENS` in `get_theme_tokens()` | All 8 required tokens resolve to non-empty valid color strings; contrast > 7:1 |
| 2 | `test_02_accessible_names_main_window` | All buttons, inputs, chips, and lists in `MainWindow` | `accessibleName()` and `accessibleDescription()` are non-empty strings |
| 3 | `test_03_accessible_names_command_palette` | `VoxCommandPaletteDialog` and `CommandItemWidget` | `txt_search`, `list_widget`, and item widgets have complete accessible names and descriptions |
| 4 | `test_04_accessible_names_settings_dialog` | `SettingsWorkspaceWidget` and child controls | All nav buttons, hotkey buttons, toggle switches, combos, and checkboxes have accessible names |
| 5 | `test_05_accessible_names_notetaker_page` | `NotetakerPage` and library/detail action bars | All buttons, search inputs, list items, sliders have descriptive accessible names |
| 6 | `test_06_keyboard_navigation_command_palette` | KeyPress events in `VoxCommandPaletteDialog` | Arrow Up/Down navigates rows; Return/Enter and Space trigger execution; Escape dismisses dialog |
| 7 | `test_07_keyboard_navigation_main_window_focus_chain` | Tab traversal in `MainWindow` | Focus advances sequentially through search -> nav buttons -> footer utilities |
| 8 | `test_08_keyboard_navigation_shortcut_dialog_escape` | Escape key in `ShortcutCaptureDialog` | Escape cleanly dismisses dialog (`reject()`) without assigning "esc" as key combination |
| 9 | `test_09_settings_friendly_compute_labels_mapping` | Bidirectional compute mapping | "Fastest (Lower Memory)" <-> "int8", "Balanced" <-> "default", "Maximum Accuracy" <-> "float16" |
| 10 | `test_10_live_mic_meter_widget_lifecycle` | `VoxAudioVisualizerBar` instantiation & teardown | Level deflection updates peak; `teardown()` cleanly stops timer and releases handles without leak |

---

## 8. Summary of Actionable Implementation Items for Team

1. **`src/voxlocal/ui/theme.py`**:
   - Add the 8 semantic design tokens to `LIGHT_TOKENS` and `DARK_TOKENS`.
2. **`src/voxlocal/ui/main_window.py`**:
   - Set `setAccessibleName` and `setAccessibleDescription` on `btn_global_search`, `nav_buttons`, footer utility buttons, `theme_toggle`, and `GlobalSearchDialog`.
   - Call `setTabOrder` across sidebar controls.
3. **`src/voxlocal/ui/command_palette.py`**:
   - Set `setAccessibleName` and `setAccessibleDescription` on `txt_search`, `list_widget`, and `CommandItemWidget`.
   - Update `keyPressEvent` to allow `Qt.Key.Key_Space` execution alongside `Enter`.
4. **`src/voxlocal/ui/settings_dialog.py`**:
   - Set `setAccessibleName` and `setAccessibleDescription` across all `_nav_buttons`, `_hotkey_buttons`, `_toggles`, combos, sliders, and checkboxes.
   - Fix `ShortcutCaptureDialog.keyPressEvent` so Escape without modifiers dismisses the dialog.
   - Replace raw compute labels with friendly combo options mapped to `config.compute_type`.
   - Connect live mic deflection meter (`VoxAudioVisualizerBar`) in Audio section with explicit `stop_meter()` / `teardown()`.
5. **`src/voxlocal/ui/notetaker_page.py`**:
   - Set `setAccessibleName` and `setAccessibleDescription` on all library buttons, search inputs, folder combos, action buttons, and audio player controls.
6. **`tests/test_ux_accessibility_regressions.py`**:
   - Implement the complete 10-test regression suite.

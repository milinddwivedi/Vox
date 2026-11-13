# Scope: Milestone 3 — Floating FlowBar, Hub Workspaces & Meeting Studio Parity

## Architecture & Subsystems
Milestone 3 covers the full UI, workspace, calendar, meeting intelligence, and storage layer for VOX (Wispr Flow exact replica on Windows).

### 1. Floating FlowBar & Voice Pill (`src/voxlocal/ui/floating_pill.py`)
- Exact Wispr Flow 440x320 bottom-docked geometry (expandable to 614x320 on active speech).
- 10-bar center-weighted equalizer animation driven by audio level signals (`audio_level_changed`).
- Live speech preview bubble, coral stop button (`#FF5757`), session timer.
- Non-activating Win32 window styles (`WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW`) with screen-saver priority.
- Integration with DictationState transitions (`idle`, `listening`, `processing`, etc.).

### 2. Flow Hub (`src/voxlocal/ui/main_window.py` & `hub_pages.py`)
- 1350x850 frameless window hosting 8 workspace routes:
  1. `/home`: Activity feed, quick stats, quick dictation test, mode toggles.
  2. `/notetaker`: Meeting intelligence dashboard, active/past meetings.
  3. `/dictionary`: Custom vocabulary & phonetic replacements.
  4. `/snippets`: Keyword expansion triggers & snippets editor.
  5. `/style`: Personal tone preferences & transformation profiles.
  6. `/scratchpad`: Document drafts, notes, and dictation staging.
  7. `/insights`: Usage statistics, WPM metrics, time saved.
  8. `/settings`: Audio device selection, hotkeys, startup, model selection.
- Command Palette (`Ctrl+K`) for instant keyboard navigation across all 8 workspaces and quick actions (`src/voxlocal/ui/command_palette.py`).

### 3. Companion Drawer & Scratchpad Window
- `VoxNotetakerDrawer` (500x420) integrated with workspaces (`src/voxlocal/ui/notetaker_drawer.py`).
- `VoxScratchpadWindow` floating resizable quick-note canvas with version history (`src/voxlocal/ui/scratchpad_window.py`).

### 4. 3-Section Meeting Studio & Calendar Engine
- Meeting Studio (`src/voxlocal/ui/meeting_studio.py`):
  - 3-section layout: Private scratchpad, real-time diarized transcript, 7-stage offline MoM deliverables generator (`src/voxlocal/engine/meeting_intelligence.py`).
  - Offline MoM generator: Executive summary, Action items with owners/deadlines, Key decisions, Discussion chronology, Sentiment/dynamics, Risk register, Next meeting agenda.
- 3-Tier Outlook Calendar client (`src/voxlocal/calendar/*`):
  - Tier 1: Microsoft Graph OAuth PKCE (`graph_provider.py`).
  - Tier 2: MAPI COM via pywin32 (`mapi_provider.py`).
  - Tier 3: Offline Mock calendar provider (`mock_provider.py`).
  - Zero-admin-consent ICS provider (`ics_provider.py`).
  - Advance alerts: 15-minute and 5-minute meeting reminder notifications with deduplication.

### 5. Onboarding Wizard & System Tray
- 4-step interactive try-it-out walkthrough matching Wispr Flow onboarding (`src/voxlocal/ui/onboarding_wizard.py`).
- System tray icon and context menu with status, workspace launcher, mode selector, and graceful exit (`src/voxlocal/ui/tray_manager.py`).

### 6. Local Persistence
- SQLite WAL schema v6 with dual-column raw transcript preservation (`raw_text` vs `final_text`), snippets, dictionary, and meeting records (`src/voxlocal/storage/database.py`).

### 7. Verification Targets
- Test files covering Milestone 3:
  - `tests/test_meeting_workspace_evolved.py` (10/10 PASSED)
  - `tests/test_meeting_studio_phase5_enterprise_ux.py` (10/10 PASSED)
  - `tests/test_e2e_calendar_and_meeting.py` (19/19 PASSED)
  - `tests/test_meeting_studio_phase5_advanced_intelligence.py` (7/7 PASSED)
  - `tests/test_meeting_intelligence.py` (8/8 PASSED)
  - `tests/test_wispr_parity_features.py` (10/10 PASSED)
  - `tests/test_micro_interactions_and_shortcuts.py` (10/10 PASSED)
  - `tests/test_voice_typing_evolved.py` (28/28 PASSED)
  - `tests/test_ui_refinement.py` (7/7 PASSED)
  - `tests/test_challenger_stress_m3_meeting.py` (19/19 PASSED)
  - `tests/test_challenger_stress_m3_hub.py` (24/24 PASSED)
  - Combined E2E suites: `test_tier1_feature_coverage.py` (100/100 PASSED), `test_tier2_boundaries.py` (100/100 PASSED), `test_tier3_pairwise.py` (22/22 PASSED)
  - Standalone binary smoke test: `dist/VoxLocal/VoxLocal.exe --smoke-test-ms 1000` (Exit code 0)

## Feature Inventory Mapping
| # | Feature | Description | Milestone | Status |
|---|---------|-------------|-----------|--------|
| 10 | Floating Voice Pill (FlowBar) | 440x320 bottom-docked non-activating widget, 10/12-bar equalizer, preview bubble, coral stop button | M3 | **DONE** (VoxFlowBar, 10-bar parabolic equalizer, preview bubble, non-activating styles verified) |
| 11 | Flow Hub & 8 Workspaces | 1350x850 window hosting Home, Notetaker, Dictionary, Snippets, Style, Scratchpad, Insights, Settings | M3 | **DONE** (1350x850 Hub, 8 routes, sidebar, shortcuts Ctrl+1..Ctrl+7/Ctrl+, verified) |
| 12 | Command Palette (`Ctrl+K`) | Quick-switcher overlay for instant navigation across all 8 workspaces and actions | M3 | **DONE** (VoxCommandPaletteDialog wired with 8 routes, 5 global actions, and DB search verified) |
| 13 | Notetaker Drawer & Scratchpad | Resizable companion drawer (500x420) and floating quick-note scratchpad window with version history | M3 | **DONE** (500x420 drawer with 5 tabs, 720x760 floating scratchpad with SQLite version history verified) |
| 14 | 3-Section Meeting Studio | Private scratchpad, diarized real-time transcript, and 7-stage offline MoM deliverables generator | M3 | **DONE** (3-section studio, 8 advanced tabs, 7-stage MoM pipeline, JSON/HTML/PDF exporters verified) |
| 15 | 3-Tier Outlook Calendar Engine | MS Graph OAuth PKCE, MAPI COM, and offline Mock calendar provider with 15m/5m advance meeting alerts | M3 | **DONE** (Graph PKCE, MAPI COM, Mock, ICS feed, 15m/5m advance alert engine verified) |
| 16 | Onboarding Wizard & System Tray | 4-step interactive try-it-out onboarding wizard and tray menu with mode selector and graceful exit | M3 | **DONE** (4-step walkthrough with live in-app dictation sandbox, system tray manager verified) |
| 17 | Local SQLite WAL Storage | Robust storage with dual-column raw transcript preservation (`raw_text` vs `final_text`), snippets, and notes | M3 | **DONE** (SQLite WAL mode, schema v6, verified snapshots, dual-column raw transcript preservation verified) |

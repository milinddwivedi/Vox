# VOX — Handoff for Cline

Written by the previous agent (Antigravity) on 2026-11-14. Read all of this before you change anything.

---

## 0. Project basics

- **App:** VOX / VoxLocal, a Windows desktop voice assistant written in Python 3.13 + PyQt6. It does dictation, a meeting notetaker, and calendar integration.
- **Entry point:** `main.py`, which creates the floating pill (`DualTierFloatingPill`) and the main window.
- **Python:** always use `.\.venv\Scripts\python.exe`. **Never delete `.pyenv/`**, because `.venv` depends on it.
- **Run from source (quickest way to check UI changes):** `.\.venv\Scripts\python.exe main.py`
- **Packaged app:** `dist\VoxLocal\VoxLocal.exe`, built with `.\.venv\Scripts\python.exe -m PyInstaller --noconfirm VoxLocal.spec`
  - **Close VOX first, including the tray icon.** If `VoxLocal.exe` is running, PyInstaller fails at the `COLLECT` step with `PermissionError ... codeccontext.pyd`.
  - Delete `build\` after every build.
  - The user runs the `.exe`. **Source changes do not show up in the `.exe` until you rebuild it.** This has confused the user before, so state clearly which one you changed.
- **Size limit:** the whole folder must stay **under 8 GB**. It is about 7.96 GB now, mostly `data/recordings`, models, `.venv` and `.pyenv`.
- **Tests:** `.\.venv\Scripts\python.exe -m pytest -q` (config is in `pytest.ini`)

---

## 1. TASK A: Sync Git/GitHub with the local cleanup (do this first)

`git status` shows uncommitted work on top of `ee28365` (`origin/main`).

**Deleted on purpose (repo cleanup):**
- the whole `.agents/teamwork/**` folder (old agent scratch notes)
- root reports `VOX_*.md`, `PROJECT.md`, `TEST_READY.md`, `TEST_REPORT.md`, `CHECKPOINT_PAUSE.md`, `baseline_results.txt`
- `tests/test_header_compliance.py` (it checked for the deleted docs)

**Kept:** the Wispr reference material from those deletions was copied into **`docs/wispr_reference/`** (new folder). See Task B.

**Modified on purpose (unified personal + work calendar sign-in, model cleanup):**
- `src/voxlocal/calendar/graph_provider.py`
- `src/voxlocal/core/config.py`
- `src/voxlocal/core/controller.py`
- `src/voxlocal/ui/calendar_connect_dialog.py`
- `src/voxlocal/engine/llm_engine.py`
- `src/voxlocal/engine/transcriber.py`
- `src/voxlocal/ui/floating_pill.py`
- `VoxLocal.spec`
- `tests/test_unified_calendar_engine.py`

**Steps:**
1. `.\.venv\Scripts\python.exe -m pytest tests/test_unified_calendar_engine.py -q`. Expect 9 passed.
2. Make sure nothing secret or large gets staged. `.gitignore` already excludes `data/`, `dist/`, `build/`, `.venv/`, `.pyenv/`, model weights, media files and `.env`. Check `git status` for anything unexpected, such as `*.dat` token files or `data/config.json`.
3. Stage and commit:
   ```powershell
   git add -A
   git status --short          # review the list
   git commit -m "chore: repo cleanup (remove agent scratch + legacy reports), preserve Wispr refs in docs/; feat(calendar): unified personal+work Microsoft sign-in"
   git push origin main
   ```
4. Run `git status` again and confirm it is clean.

---

## 2. TASK B: Floating pill / toggle bar ("FlowBar"): exact Wispr Flow parity

**Goal from the user:** the floating icon and toggle bar must look, move and behave **exactly like Wispr Flow's**. That covers the same states, sizes, animations, hover behaviour, controls, wording and responsiveness. The user says many features are missing.

### 2.1 Where the code is

| File | What it holds |
|---|---|
| `src/voxlocal/ui/floating_pill.py` (about 2,100 lines) | **The main file.** `VoxFlowBar` (L635) is the pill; `DualTierFloatingPill = VoxFlowBar` (L2105) |
| ↳ `PillState` enum (L137) | `IDLE, RESTING, READY, LISTENING, PROCESSING, POLISH_PROCESSING, COMMITTED, ERROR, MINI_MEETING, CARD_NOTIFICATION, ACTIVE_RECORDING, L4_PREVIEW` |
| ↳ `DIMENSION_SPECS` (about L169), `MORPH_DURATION_MS=420`, `FADE_OUT_MS`, `FADE_IN_DELAY_MS` | Size and timing for each state |
| ↳ `_build_ui` (L758), `set_state` (L1469), `_on_morph_step/_finished` (L1138) | How the layout is built and how it morphs between states |
| ↳ `enterEvent/leaveEvent` (L1729/1750), `mousePressEvent` (L1914) | Hover expand/collapse, dragging, clicks |
| ↳ `DynamicWaveformCapsule` (L220), `SessionTimerWidget`, `FlowBarTooltipPill` (L343), `LiveSpeechPreviewBubble` (L396), `_RoundPillButton`, `PlatformBadgeWidget`, `JoinButton` | Sub-widgets |
| ↳ `show_meeting_reminder` (L1225), `_on_calendar_upcoming_changed` (L1313), `_on_controller_*` handlers | Signals from the controller (dictation, meeting and calendar state) |
| `src/voxlocal/ui/floating_widget.py` | `FloatingWidget(VoxFlowBar)` subclass, `MeetingQuickStartDialog` |
| `src/voxlocal/ui/notetaker_drawer.py` | **Live Notetaker side-by-side window** shown on the left of `Notetaker comparision.mp4` (`VOX — Notetaker`: header, tabs, transcript view, summary view, bottom bar) |
| `src/voxlocal/ui/animations.py` | `VoxMotion` easing/timing, `PulseBeacon`, equalizer/waveform visualizers |
| `src/voxlocal/ui/theme.py` | Design tokens (light/dark). Use these and do not hard-code colours |
| `src/voxlocal/core/controller.py` | Emits the signals the pill listens to (search for `floating_pill`, around L764, L800, L910, L1420, L3682) and runs live progressive meeting transcription (`_progressive_transcribe_worker` at L2756) |
| `main.py` (L185–L272) | Creates the pill and connects it to the controller and main window |

### 2.2 Reference material: what "exactly like Wispr" means

Read these in order:
1. **`docs/wispr_reference/NOTETAKER_VIDEO_COMPARISON.md`** and the 5 extracted keyframes in **`docs/wispr_reference/notetaker_frames/`**: **READ THIS FIRST.** This is the forensic side-by-side breakdown of `Notetaker comparision.mp4` (VOX on the left vs. Wispr Flow on the right during a live 11-person Zoom meeting), covering the floating pill overlay (`floating_pill.py`), the live Notetaker window (`src/voxlocal/ui/notetaker_drawer.py`), live speaker labeling (`Them` / `You` instead of noisy `Speaker 1..8`), chat-bubble transcript cards, and the persistent bottom bar (`Stop` + `Ask anything, or type / for Skills` + `What did I miss?`).
2. `docs/wispr_reference/ux_survey_report.md`: **§3 is the 8-state VOX-vs-Wispr comparison matrix** and §4 is the mini-toggle and expanded-card spec with a CSS→Qt property mapping.
3. `docs/WISPR_FLOW_FORENSIC_ARCHITECTURE.md`: **§2, "Floating Bar (FlowBar) Exact Design, Dimensions & Functioning"**, and §4, "why Wispr looks modern".
4. `docs/wispr_reference/wispr_pill_css.txt` and `wispr_pill_semantic_css.txt`: Wispr's pill styling as extracted (sizes, radii, colours, transitions).
5. `docs/WISPR_FLOW_REBUILD_SPEC.md`: §1 palette tokens and typography, §2 window topology and geometry.
6. `docs/wispr_reference/VOX_WISPR_FLOW_VIDEO_ANALYSIS.md` and `VOX_WISPR_FLOW2_ANALYSIS.md`: frame-by-frame behaviour taken from screen recordings of Wispr Flow.
7. The real app is installed at `C:\Users\milin\AppData\Local\WisprFlow\`. **Use it for reference only: read it and observe it.** Do not copy its proprietary assets, icons or code into VOX. Rebuild the behaviour natively in PyQt6.

### 2.3 How to do it

1. **Write the gap list first** in `docs/FLOWBAR_GAP_LIST.md`. For every Wispr state and feature in the reference docs, mark VOX as ✅ matches, ⚠️ partial or ❌ missing, and cite the line in `floating_pill.py`. **Show the list to Milind and ask him to add the missing features he has noticed** before you start coding.
2. Then fix the gaps one at a time:
   - idle/resting look
   - hover expansion
   - listening waveform responsiveness
   - processing and committed feedback
   - error state
   - meeting mini-bar and reminder card
   - tooltips and terminology
   - drag/snap position
   - DPI scaling
   - dark/light mode
3. Keep the `PillState` names and the controller signal contracts. Several tests and the controller depend on them.
4. Animations must stay smooth. No blocking work on the UI thread; heavy work goes in a thread and talks to the UI through Qt signals.

### 2.4 Verify

- Tests that cover the pill (they must keep passing, and add new ones for new behaviour):
  - `tests/test_toggle_bar_states_e2e.py`
  - `tests/test_toggle_bar_bugfixes.py`
  - `tests/test_adversarial_challenger_m6_flowbar.py`
  - `tests/test_phase11_product_experience_and_workflow.py`
- Check it by eye: run `.\.venv\Scripts\python.exe main.py` and trigger each state (dictation hotkey, a meeting, a calendar reminder), in both light and dark themes.
- Finally, **close VOX, rebuild the `.exe`, delete `build\`**, and tell Milind to launch `dist\VoxLocal\VoxLocal.exe`.

---

## 3. Rules the user has set

- **UI/UX must stay consistent** with the existing theme tokens (`theme.py`) across the whole app.
- **Calendar:** one combined sign-in screen per provider (Microsoft, Google, Zoom). **Never split the UI into "Personal" vs "Work".** The backend works out which kind of account it is (`MsGraphCalendarProvider.detect_account_type`). Never mark a provider as connected unless a real API call succeeded, and never show made-up meetings.
- Keep the folder **under 8 GB**. Don't commit `data/`, models or recordings.
- The user is not technical. Explain results in plain language and say whether they need to restart VOX or rebuild the `.exe`.

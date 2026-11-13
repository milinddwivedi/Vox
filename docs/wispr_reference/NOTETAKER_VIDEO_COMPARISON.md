# Forensic Comparison: `Notetaker comparision.mp4` (VOX vs. Wispr Flow)

This document captures the exact side-by-side differences observed in `Notetaker comparision.mp4` (`1918x1078`, `177.6s` / `2m 57s`), where:
- **Left Panel:** `VOX — Notetaker` (`src/voxlocal/ui/notetaker_drawer.py`)
- **Center Panel:** Live Zoom Meeting (`STRATA x WFX - Implementation & Planning`, 11 participants, screen-sharing an Excel apparel categorization sheet)
- **Right Panel:** **Wispr Flow Notetaker** (the benchmark target UI/UX)

Reference keyframes extracted from the video are saved in `docs/wispr_reference/notetaker_frames/`:
- `01_live_transcript_side_by_side.jpg` (`t=0.0s`)
- `02_wispr_autoshare_popover.jpg` (`t=92.0s`)
- `03_wispr_scrolled_jump_to_latest.jpg` (`t=106.0s`)
- `04_vox_summary_empty_during_meeting.jpg` (`t=121.0s`)
- `05_wispr_copy_transcript_tooltip.jpg` (`t=136.0s`)

---

## 1. Floating Pill / Toggle Bar Overlay Differences (Visible Live in the Video)

| Feature | VOX (Left Side of Screen) | Wispr Flow (Right Side of Screen) | Code Location in VOX |
|---|---|---|---|
| **Placement & Orientation during Meeting** | Horizontal pill (`• 03:48 Notetaker`) floating on the left edge at `y ≈ 500px`, **overlapping on top of VOX's own transcript text** (`[02:28] Speaker 3: Okay`, `[04:07] Speaker 5: ...`). | Docked vertically on the **right screen edge** (`~28×76px` vertical black capsule with green border/glow, top mini waveform icon, and bottom green status dot) — never obscures the transcript text. | `src/voxlocal/ui/floating_pill.py` (`VoxFlowBar`, `DIMENSION_SPECS`, `_reposition_to_anchor`) |
| **Visual Content of Active-Meeting Pill** | Shows a red dot + timer + literal word `"Notetaker"` (`• 03:48 Notetaker`). | Compact, distraction-free vertical edge pill with live audio bars and an emerald indicator dot (no redundant `"Notetaker"` label, timer is inside the Notetaker window's transcript bar). | `src/voxlocal/ui/floating_pill.py` (`ACTIVE_RECORDING` state) |

---

## 2. Notetaker Window Header & Meeting Metadata

| Feature | VOX (`notetaker_drawer.py`) | Wispr Flow (Right Panel) | What Needs to Change in VOX |
|---|---|---|---|
| **Meeting Title** | Generic white input box reading `"Meeting Notes"` (disconnected from the actual Zoom meeting title). | Large, editorial serif heading automatically populated from Calendar / Zoom window: **`STRATA x WFX - Implementation & Planning`**. | Auto-populate title from active Calendar event (`UnifiedCalendarEngine`) or detected Zoom/Teams window title, styled as a clean borderless heading (editable on click). |
| **Attendee Avatars & Metadata Row** | **Missing.** Instead shows raw technical telemetry: `Mic: Live (0%)  Call Audio: Live (64%)  Call-Only Privacy Guard`. | Directly below the title, shows a warm row of **participant initial chips** (`M` `A` `B` `D` `+7`), **`Today`**, **`Add folder`** button, and an **Auto-share** paper-plane button. | Replace the debug-style `Mic: Live (0%) Call Audio: Live (64%)` row in `notetaker_drawer.py` with the Calendar/Zoom participant badge strip (`M A B D +7 · Today · Add folder`). |
| **Top Action Bar & Auto-Share Popover** | Crowded row of 3 heavy white buttons (`Expand`, `Pause`, `Stop`) next to the title box, plus another `Full View` button one row below (`Expand` and `Full View` are redundant!). | Clean top bar with `←` (back), `•••` (more), **`Share`** pill button, and **`🔗` (Copy link)** button. Clicking the paper-plane icon opens an **Auto-share popover** (`Auto-share this meeting's notes with: No one | @worldfashionexchange.com | All attendees`). | Remove duplicate `Expand` / `Full View` clutter; move `Stop` to the bottom bar (like Wispr); add `Share` / `Copy` actions in header. |
| **Theme & Visual Warmth** | Stark high-contrast dark header (`#111`) colliding with a cold pure-white (`#FFFFFF`) transcript box. | Warm cream/sand surface (`#FAF9F6` / `--sand-50`) throughout the entire window with soft beige cards (`#F3F1EC`). | Apply the warm Sand palette (`src/voxlocal/ui/theme.py`) consistently across `notetaker_drawer.py`. |

---

## 3. Tab Bar & Navigation Taxonomy

| Feature | VOX (`notetaker_drawer.py`) | Wispr Flow (Right Panel) | What Needs to Change in VOX |
|---|---|---|---|
| **Tabs Shown During Live Meeting** | **5 tabs:** `My Notes`, `Transcript`, `Summary`, `Tasks`, `Ask AI` + `Full View` button. | **3 clean tabs** with subtle icons and an underline indicator: **`My thoughts`**, **`llı Transcript`**, **`✦ Summary`**. | Rename `My Notes` → **`My thoughts`**, reduce tab bar to those 3 primary tabs (`My thoughts`, `Transcript`, `Summary`), and move `Ask AI` into the persistent bottom bar! |
| **Behavior of `Summary` Tab** | At `02:01` (`04_vox_summary_empty_during_meeting.jpg`), clicking `Summary` shows a dead empty screen: *"Meeting summary will appear automatically once finalized."* | Uses `✦ Summary` tab (which can also be triggered on-demand or post-meeting), while live questions (`"What did I miss?"`) are handled in the bottom Ask bar at any point during the call. | Allow on-demand "Generate live summary so far" inside `Summary` instead of a blank dead-end page during a live meeting. |

---

## 4. Live Transcript View & Speaker Labeling

| Feature | VOX (`notetaker_drawer.py`) | Wispr Flow (Right Panel) | What Needs to Change in VOX |
|---|---|---|---|
| **Visual Format of Transcript** | Raw plain-text log in a single `QTextEdit` box: `[03:18] Speaker 2: I think it's all right...` | **Chat-bubble cards** on a warm cream canvas: each utterance is its own soft rounded beige pill (`#F2EFE9`, `border-radius: 10px`, comfortable padding), grouped under a section label like **`Them`** (for remote call audio) or **`You`** (for mic audio). | Render transcript turns as rounded bubble cards grouped by speaker (`Them` / `You` during live capture, refined after meeting) instead of raw `[MM:SS] Speaker N:` console log lines. |
| **Fake Over-Segmented Speaker Numbers vs Honest Live Attribution** | VOX's live diarizer hallucinates **8 different speakers** (`Speaker 1` through `Speaker 8`) for the **same single person speaking** across consecutive sentences (`[02:55] Speaker 5` → `[03:09] Speaker 7` → `[03:18] Speaker 2` → `[03:31] Speaker 6`)! | Wispr Flow displays a calm banner: **`After the meeting, Wispr will improve the transcript & label speakers`**, and during the live call cleanly labels remote audio as **`Them`** (and local mic as **`You`**) instead of guessing noisy per-chunk speaker numbers! | In `controller.py` / `transcriber.py`, stop assigning noisy per-chunk `Speaker 1..8` IDs during 3-second live chunks! During live capture, attribute remote loopback stream as **`Them`** and mic stream as **`You`**, and run full speaker clustering + refinement when the meeting ends (with the same info banner). |
| **Transcript Sub-Toolbar** | Full-width text box `Filter live transcript or speaker...` permanently taking vertical space. | Compact rounded toolbar bar showing: **`🕒 14:00` (live timer)** on the left, and **3 icon buttons** on the right: **`🔍` (Search toggle)**, **`🌐/A` (Language/Translate)**, and **`📋` (`Copy transcript` with instant tooltip)**. | Replace the bulky search bar + header timer with Wispr's compact `[ 🕒 14:00      🔍  🌐  📋 ]` status bar + dismissible info banner (`After the meeting, VOX will improve the transcript & label speakers  ✕`). |
| **Scroll-to-Bottom Floating Button** | When scrolling up (`01:46`), there is no floating button to jump back to live speech. | When scrolled up (`03_wispr_scrolled_jump_to_latest.jpg`), a circular **`↓` floating button** appears at the bottom-right of the transcript list. | Add a floating `↓` button when the user scrolls up from the bottom of the live transcript. |
| **Live Transcription Accuracy & Context Continuity** | Noticeable word errors at chunk boundaries: `"take a look at tea"` (instead of *"take a look at tops"*), `"sports ball"` (instead of *"sports bra"*), `"talk"` (instead of *"tops"*), `"Track moto coach fire"`. | Much cleaner sentence continuity (`"Let's take a look at— let's look at this section here, tops"`, `"sports bra, bodysuit would be an intimate"`). | Pass rolling previous-turn text as `initial_prompt` across progressive live chunks and use overlapping window stitching (`controller.py:_progressive_transcribe_worker`) so words aren't cut in half at chunk edges. |

---

## 5. Persistent Bottom Action Bar (Stop + Live Ask AI + "What did I miss?")

| Feature | VOX (`notetaker_drawer.py`) | Wispr Flow (Right Panel) | What Needs to Change in VOX |
|---|---|---|---|
| **Bottom Bar Layout** | **None.** VOX has no bottom bar; `Stop` is at the top-right and `Ask AI` is hidden inside a separate tab. | Persistent bottom bar visible across tabs containing:\n1. **`⏹ Stop`** pill button (green square icon + `"Stop"`)\n2. **`Ask anything, or type / for Skills`** input field (always accessible while reading the transcript!)\n3. **`What did I miss?`** one-click quick-action pill button\n4. Subtle consent footer above it: *`Always get consent when transcribing others.`* | Add this exact persistent bottom bar (`[ ⏹ Stop ] [ Ask anything, or type / for Skills ] [ What did I miss? ]`) to `notetaker_drawer.py` so the user can ask AI or click *"What did I miss?"* without leaving the live transcript! |

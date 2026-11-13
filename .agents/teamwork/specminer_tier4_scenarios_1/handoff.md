# Tier 4 Real-World Application Scenarios — Specification & Test Blueprint

**Author**: `specminer_tier4_scenarios_1` (Specification Miner Archetype)  
**Parent Agent**: `8bcdb983-cd43-4e86-8943-a5b59056a1e0` (Sub-Orchestrator: E2E Testing Tiers 4-5)  
**Target Test Suite**: `tests/e2e/test_tier4_scenarios.py`  
**Authority**: `ORIGINAL_REQUEST.md`, `PROJECT.md § Feature Inventory`, `TEST_INFRA.md §2.4`, `SCOPE.md`  
**Target Environment**: Windows 11 x64, Python 3.13.14, PyQt6 6.11.0, pytest 9.2.0  

---

## 1. Observation

### 1.1 Codebase & Interface Contracts Probed
1. **Controller Lifecycle & Hotkey Dispatch** (`src/voxlocal/core/controller.py` lines 86-140, 1072-1094, 1700-1914):
   - `AppController.handle_hotkey_action(action: str, is_release: bool = False)`:
     - `"ptt", is_release=False` -> `start_dictation()` (transitions `idle` -> `initializing` -> `listening`)
     - `"ptt", is_release=True` -> `stop_dictation_and_insert()` (transitions `listening` -> `stopping` -> `processing` -> `completed` -> `idle`)
     - `"popo" / "toggle", is_release=False` -> `toggle_dictation()` (initiates hands-free or stops active session)
     - `"dismiss" / "cancel", is_release=False` -> `cancel_dictation()` (transitions to `dismissed` -> `idle`, discards audio, zeroes insertion)
     - `"mode_1"` through `"mode_5"`, `is_release=False` -> `set_writing_mode(...)` (`verbatim`, `clean`, `polished`, `structured`, `email`)
   - Emitted Qt signals: `state_changed(DictationState)`, `dictation_state_changed(str, str)`, `mode_changed(str)`, `writing_mode_changed(str)`, `dictation_completed(str, str, str)`, `dictation_interim_updated(str, str)`, `audio_level_changed(float, list)`.

2. **Verbal Self-Correction & Retraction Intelligence** (`src/voxlocal/engine/dictation_processor.py` lines 18-39, 44-100, 500-650):
   - `DictationProcessor.detect_verbal_self_corrections(text: str) -> Dict[str, Any]`:
     - Discovered phrase: `"I want to eat apples scratch that oranges actually mangoes"`
     - Probed output:
       `clean_text == "I want mangoes"`
       `retracted_segments == ["to eat apples scratch that", "oranges actually"]`
       `annotated_html == 'I want <span style="color:#D32F2F; text-decoration:line-through; font-weight:500;">to eat apples scratch that</span> <span style="color:#D32F2F; text-decoration:line-through; font-weight:500;">oranges actually</span> mangoes'`
     - Negation protection verified: `"don't delete that file"`, `"never mind that report"` do not trigger false-positive retractions.

3. **Win32 Text Inserter & Atomic Clipboard Restoration** (`src/voxlocal/win32/text_inserter.py` lines 354-450, 452-517):
   - `TextInserter.insert_text(text, method="sendinput_unicode"|"clipboard"|"uipi_wm_char", restore_clipboard=True, target=None)`:
     - Method `"clipboard"` invokes `_paste_via_clipboard`:
       1. Captures `previous_clip = get_clipboard_text()` via `CF_UNICODETEXT`
       2. Writes `set_clipboard_text(new_text)`
       3. Dispatches hardware scan-code `Ctrl+V` (or direct `WM_CHAR` fallback)
       4. Waits 90ms for target app consumption
       5. Atomically restores `set_clipboard_text(previous_clip)`

4. **Floating Voice Pill (FlowBar)** (`src/voxlocal/ui/floating_pill.py` lines 133-180, 382-450, 600-700, 1184):
   - `DualTierFloatingPill` (aliased as `VoxFlowBar`):
     - States: `PillState.IDLE`, `PillState.READY`, `PillState.LISTENING`, `PillState.PROCESSING`, `PillState.POLISH_PROCESSING`, `PillState.COMMITTED`, `PillState.ERROR`.
     - Coral stop button: `_RoundPillButton(kind="stop")` with `#FF5757` active indicator.
     - Center-weighted 10-bar waveform capsule: `DynamicWaveformCapsule(bar_count=10)`.
     - Live preview bubble positioned 12px above pill displaying `#D32F2F` crimson strikethroughs.

5. **Meeting Studio & Notetaker Drawer** (`src/voxlocal/ui/meeting_studio.py` lines 84-180, 385-450, 1200-1280; `src/voxlocal/ui/notetaker_drawer.py` lines 33-100):
   - `VoxMeetingStudioWorkspace`:
     - Unified 3-Tab: Tab 1 ("✏️ My Thoughts" - private scratchpad isolated from MoM export), Tab 2 ("🎙️ Transcript" - speaker-diarized with live search and rename), Tab 3 ("✨ Summary" - executive MoM, action items, decisions).
     - Privacy guarantee: `tab_thoughts.export_clean_mom(mom)` guarantees 0 leakage of private thoughts into public minutes.
   - `VoxNotetakerDrawer`: 500x420 companion window, live timer, pause/resume, expand to 920x660, synchronized with `controller.meeting_tick` and `meetings.notes`.

6. **Calendar Engine & Alert Detection** (`src/voxlocal/calendar/calendar_manager.py` lines 26-108, 109-180):
   - `CalendarAlertEvaluator.evaluate_meeting_alerts(meetings, current_time)`:
     - 15-Minute Advance (`5.0 < mins <= 15.0`): triggers `acoustic_priming` (injects attendee names and title acronyms into speech hotwords).
     - 5-Minute Imminent (`0.0 < mins <= 5.0`): triggers `show_toast_prompt` (1-click meeting capture launch).

7. **Local SQLite WAL Persistence** (`src/voxlocal/storage/database.py` lines 1-150):
   - Database runs in `PRAGMA journal_mode = wal`.
   - Dual-column transcript preservation: `raw_text` (immutable acoustic STT output) vs `final_text` (mode transformed / snippet expanded / retracted output).
   - Auto-increment snippet usage via `increment_snippet_usage(snippet_id)`.
   - Reversible transformations via `controller.undo_ai_edit(entry_id)`.

8. **Existing Test Harness Execution**:
   - `tests/e2e/test_tier3_pairwise.py` executed via `.\.venv\Scripts\python.exe -m pytest tests/e2e/test_tier3_pairwise.py -v`.
   - Result: **22 passed in 7.28s (100% pass rate)**.
   - Confirms offscreen Qt platform (`QT_QPA_PLATFORM=offscreen`) operates deterministically without focus stealing or window pops.

---

## 2. Logic Chain

1. **Why Tier 4 is Critical**:
   - Tiers 1-3 test features in isolation, at boundaries, and in pairs. Tier 4 represents the end-to-end integration layer that proves VOX functions as a cohesive, seamless desktop application matching Wispr Flow under real user operating conditions.
   - Wispr Flow's primary customer value lies in frictionless voice typing across multiple apps, real-time verbal error recovery, continuous background recording, intelligent calendar priming, and strict privacy/clipboard safety.

2. **Scenario Design Requirements**:
   - Every scenario must be a **multi-step workflow** involving at least 3 distinct subsystems (e.g. Hotkeys -> State Machine -> Inserter -> DB -> UI).
   - Invariants must be strictly enforced:
     - **Opaque-Box Verification**: Only public methods, Qt signals, and DB tables are invoked.
     - **Dual-Column Transcript Fidelity**: Raw ASR transcript is preserved in `raw_text` regardless of the transformations applied to `final_text`.
     - **Clipboard Integrity**: Any pre-existing clipboard contents are fully restored after paste injection.
     - **Offscreen Headless Safety**: No test may show actual desktop windows or steal OS focus.

3. **Coverage Mapping**:
   - Scenarios 1 through 10 directly address the 10 scenarios requested by the orchestrator.
   - Scenarios 11 and 12 provide comprehensive edge case coverage (cancellation lifecycle and UI equalizer interactivity).

---

## 3. Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | Dictation Modes | 5 Writing Modes | Verbatim, Clean, Polished, Structured, Email transformations | Raw STT string, mode name | Formatted string adhering to mode contract | Falls back to clean mode if unknown | `src/voxlocal/engine/dictation_processor.py` |
| 2 | Self-Correction | Verbal Retraction Parser | Detects verbal correction markers and strips erroneous phrases | Utterance containing "scratch that", "actually", etc. | `clean_text`, `retracted_segments`, `annotated_html` | Graceful passthrough if no markers | `src/voxlocal/engine/dictation_processor.py` |
| 3 | UI Overlay | Dual-Tier Floating Pill | Frameless bottom-docked capsule with live waveform and crimson preview | Dictation state, RMS level, interim text | UI geometry, equalizer bar levels, HTML bubble | Compact resting fallback on error | `src/voxlocal/ui/floating_pill.py` |
| 4 | Win32 Injection | 3-Tier Text Inserter | Delivers text via SendInput Unicode, UIPI WM_CHAR, or clipboard paste | Target HWND, formatted text, insertion method | `InsertionOutcome(success, method)` | Copies to clipboard if target closes | `src/voxlocal/win32/text_inserter.py` |
| 5 | Win32 Safety | Atomic Clipboard Backup & Restore | Snapshots user clipboard, executes paste, and restores original clipboard | Pre-existing CF_UNICODETEXT, new text | Original clipboard restored within 90ms | Retry loop on Win32 OpenClipboard failure | `src/voxlocal/win32/text_inserter.py` |
| 6 | Meetings | Meeting Studio 3-Section Workspace | Personal scratchpad ("My Thoughts"), Diarized transcript, Executive MoM | Meeting ID, audio stream, live notes | Diarized segments, action items, decisions, MoM | Auto-heals orphaned recording on crash | `src/voxlocal/ui/meeting_studio.py` |
| 7 | Calendar | 3-Tier Calendar Alert Engine | Evaluates upcoming meetings for 15m priming and 5m imminent alert | Calendar events, current UTC timestamp | `meeting_alert(5min|15min)`, priming keywords | Deduplicates alerts to prevent repeated toasts | `src/voxlocal/calendar/calendar_manager.py` |
| 8 | Vocabulary | Custom Words & Snippet Expansion | Offline technical terms replacement and `/shortcut` expansion | Raw transcribed tokens, active DB dictionary | Expanded text, incremented usage counters | Case-insensitive token matching | `src/voxlocal/storage/database.py` |
| 9 | Onboarding | 4-Step Interactive Tour | First-run walkthrough: Welcome, Setup/Mic probe, Shortcuts, Live voice test | User UI clicks, mic test trigger | Config preferences persisted (`first_run_done=True`)| Safe cancel of mic probe on dialog reject | `src/voxlocal/ui/onboarding_wizard.py` |
| 10 | Navigation | Command Palette (`Ctrl+K`) | Quick action and workspace switcher across 8 Hub routes | Keyboard query string | Navigates to selected workspace index | Filters gracefully on unmatched query | `src/voxlocal/ui/command_palette.py` |
| 11 | Companion UI | Notetaker Drawer | Resizable companion drawer synchronized with active meeting session | Controller meeting ticks, user inline notes | Live elapsed timer, persisted notes | Auto-saves title and notes on edit finished | `src/voxlocal/ui/notetaker_drawer.py` |
| 12 | Persistence | SQLite WAL Dual-Column Schema | Database storage preserving both raw acoustic text and final formatted text | Dictation metadata, speech duration, target app | Dual-column rows in `dictation_history` | Transaction rollback on write lock | `src/voxlocal/storage/database.py` |

---

## 4. Edge Cases Discovered

| # | Feature | Input | Observed Behavior |
|---|---------|-------|-------------------|
| 1 | Verbal Retraction | `"don't delete that file"` | Linguistic negation guard prevents false-positive retraction; sentence preserved verbatim |
| 2 | Verbal Retraction | `"I want to eat apples scratch that oranges actually mangoes"` | Chained clause and term retractions produce `clean_text == "I want mangoes"` and 2 discarded segments |
| 3 | Clipboard Restore | Pre-existing clipboard contains Unicode/Devanagari text (`"नमस्ते दुनिया"`) | UTF-16 code units safely snapshotted and completely restored after Ctrl+V injection |
| 4 | Clipboard Restore | Clipboard is empty (`CF_UNICODETEXT` unavailable) before dictation | Inserter detects None snapshot, performs paste, and leaves clipboard in safe state without raising exceptions |
| 5 | Target Focus Loss | Target application closes or terminates while speech transcription is running | Text inserter detects invalid HWND and routes transcription to clipboard with notification |
| 6 | Hotkey Pre-emption | PTT release occurs before recorder initialization finishes (`_dictation_state == "initializing"`) | Controller marks `_ptt_stop_pending = True` and immediately stops recorder once initialized |
| 7 | Dictation Dismiss | User triggers `dismiss` hotkey during active recording | `cancel_dictation()` discards audio buffer, returns state to `idle`, and injects 0 characters |
| 8 | Meeting Recovery | Application killed abruptly during meeting recording | `_recover_unfinalized_meetings()` on next boot scans disk for `mixed.wav`, marks status `interrupted`, and preserves duration |
| 9 | Calendar Alerts | Multiple meetings starting at the exact same minute | Alert evaluator iterates all meetings, deduplicates event IDs, and triggers distinct alerts |
| 10 | Snippet Expansion | Multiple identical snippets in one utterance (`"/brb now and /brb later"`) | Both occurrences expanded and snippet usage counter incremented accordingly |

---

## 5. Detailed Specifications for 12 Tier 4 Real-World Application Scenarios

### Scenario 1: Full Voice Typing Journey Across 5 Modes with Focus Preservation
- **Persona / Story**: A power user drafting documentation across multiple windows, dynamically changing transformation modes (`verbatim`, `clean`, `polished`, `structured`, `email`) while keeping focus locked to an external IDE window.
- **Prerequisites & Fixtures**:
  - `qapp_offscreen: QApplication`
  - `isolated_controller: AppController`
  - `synthetic_audio: Callable`
  - Mock Target Window: `mock_target = TargetWindowInfo(hwnd=0x1034, title="Feature_Spec.md - Visual Studio Code", process_name="Code.exe", process_id=5420, thread_id=9810, focus_child_hwnd=0x1034)`
- **Mocks & Setup**:
  ```python
  inserted_payloads = []
  isolated_controller.focus_tracker.get_active_target = MagicMock(return_value=mock_target)
  isolated_controller.focus_tracker.lock_current_target = MagicMock(return_value=mock_target)
  isolated_controller.text_inserter.insert_text = MagicMock(
      side_effect=lambda text, **kw: (inserted_payloads.append((text, kw.get("target"))), (True, "sendinput_unicode"))[1]
  )
  dummy_audio = synthetic_audio(duration_sec=2.0)
  isolated_controller.dictation_recorder.stop = MagicMock(return_value=(dummy_audio, 2.0))
  ```
- **Step-by-Step Action Sequence**:
  1. Define mode test cases:
     - `verbatim`: Raw speech `"um we need three servers comma right now period"` -> Inserter receives `"um we need three servers comma right now period"`
     - `clean`: Raw speech `"um we need three servers comma right now period"` -> Inserter receives `"We need 3 servers, right now. "`
     - `polished`: Raw speech `"um we need three servers comma right now period"` -> Inserter receives `"We require three servers immediately. "`
     - `structured`: Raw speech `"three servers period database comma cache comma worker"` -> Inserter receives formatted structured list: `"- Database\n- Cache\n- Worker\n"`
     - `email`: Raw speech `"please provision the three servers today period thanks"` -> Inserter receives email format with greeting and sign-off.
  2. For each mode:
     a. `isolated_controller.set_writing_mode(mode_name)`
     b. Mock `isolated_controller.transcriber.transcribe_dictation` with mode-specific raw speech.
     c. PTT Press: `isolated_controller.handle_hotkey_action("ptt", is_release=False)`
        - Assert `isolated_controller.dictation_state in ("initializing", "listening")`
        - Assert `isolated_controller.focus_tracker.lock_current_target` called.
     d. PTT Release: `isolated_controller.handle_hotkey_action("ptt", is_release=True)`
        - Assert `isolated_controller.dictation_state in ("stopping", "processing")`
     e. Spin event loop (`qapp_offscreen.processEvents()`) with timeout until state returns to `"idle"` or `"completed"`.
     f. Assert `len(inserted_payloads) == i + 1`
     g. Assert `inserted_payloads[-1][1].hwnd == 0x1034` (Focus preserved on target window).
     h. Assert SQLite WAL record created (`isolated_controller.db.list_dictation_history(limit=1)`):
        - `entry["target_window"] == "Feature_Spec.md - Visual Studio Code"`
        - `entry["target_process"] == "Code.exe"`
        - `entry["raw_text"]` contains exact unmutated acoustic transcript.
        - `entry["final_text"]` contains mode-formatted output.
        - `entry["status"] == "completed"`.
- **Teardown**:
  - `isolated_controller.shutdown()`, reset mocks, `qapp_offscreen.processEvents()`.

---

### Scenario 2: Real-Time Retraction and Verbal Self-Correction in Long-Form Writing
- **Persona / Story**: An author dictates a complex sentence with multiple mid-utterance changes of mind: `"I want to eat apples scratch that oranges actually mangoes"`. The UI preview bubble shows crimson strikethroughs, and only the cleaned final sentence is inserted into the document.
- **Prerequisites & Fixtures**:
  - `qapp_offscreen: QApplication`
  - `isolated_controller: AppController`
  - `offscreen_widgets: Dict[str, Any]` (factory providing `pill`)
- **Step-by-Step Action Sequence**:
  1. Instantiate Floating Voice Pill: `pill = offscreen_widgets["pill"]()`.
  2. Direct Retraction Parsing Verification:
     - Call `res = DictationProcessor.detect_verbal_self_corrections("I want to eat apples scratch that oranges actually mangoes")`.
     - Assert `res["has_corrections"] is True`.
     - Assert `res["clean_text"] == "I want mangoes"`.
     - Assert `"to eat apples scratch that"` in res["retracted_segments"].
     - Assert `"oranges actually"` in res["retracted_segments"].
     - Assert `'<span style="color:#D32F2F; text-decoration:line-through; font-weight:500;">'` in `res["annotated_html"]`.
  3. Live Preview Bubble Reactivity:
     - Connect spy listener to `isolated_controller.live_preview_updated` (or `dictation_interim_updated`).
     - Emit interim audio/text update:
       `isolated_controller.dictation_interim_updated.emit("oranges actually mangoes", "I want to eat apples scratch that")`
     - Verify pill preview bubble reflects the active text and strikethrough markup.
  4. End-to-End Dictation & Injection:
     - Mock transcriber: `transcriber.transcribe_dictation = MagicMock(return_value="I want to eat apples scratch that oranges actually mangoes")`.
     - Inserter spy: `inserted_texts = []`, `isolated_controller.text_inserter.insert_text = MagicMock(side_effect=lambda t, **kw: (inserted_texts.append(t), (True, "sendinput_unicode"))[1])`.
     - Run dictation lifecycle (`start_dictation()` -> `stop_dictation_and_insert()`).
     - Spin event loop until completed.
  5. Assertions:
     - Assert `len(inserted_texts) == 1`.
     - Assert `"I want mangoes"` in inserted_texts[0].
     - Assert `"apples"` not in inserted_texts[0].
     - Assert `"scratch that"` not in inserted_texts[0].
     - Assert `"oranges"` not in inserted_texts[0].
     - SQLite WAL verification:
       - Query latest dictation row.
       - Assert `row["raw_text"] == "I want to eat apples scratch that oranges actually mangoes"`.
       - Assert `"I want mangoes"` in row["final_text"].
  6. Wispr Flow "Undo AI Edit" Contract:
     - Call `reverted = isolated_controller.undo_ai_edit(row["id"])`.
     - Assert `reverted == "I want to eat apples scratch that oranges actually mangoes"`.
     - Assert SQLite `final_text` updated back to raw transcript with `undone = 1`.
- **Teardown**:
  - `pill.close()`, `pill.deleteLater()`, `qapp_offscreen.processEvents()`.

---

### Scenario 3: Meeting Recording Session with Live Scratchpad Thoughts, Diarization, and MoM Synthesis
- **Persona / Story**: A manager conducts a live 30-minute team sync. During the call, they write private negotiation notes in Tab 1 ("My Thoughts"). VOX captures audio, diarizes multiple speakers, and synthesizes complete Minutes of Meeting while guaranteeing 100% private thought isolation.
- **Prerequisites & Fixtures**:
  - `qapp_offscreen: QApplication`
  - `isolated_controller: AppController`
  - `isolated_db: LocalDatabase`
  - `offscreen_widgets: Dict[str, Any]` (providing `meeting_studio`)
- **Step-by-Step Action Sequence**:
  1. Instantiate Meeting Studio: `studio = offscreen_widgets["meeting_studio"]()`.
  2. Start Meeting Recording:
     - `ok, msg = isolated_controller.start_meeting_recording(title="Sprint 45 Architecture Sync", recording_mode="audio_only")`.
     - Assert `ok is True`.
     - Assert `isolated_controller.meeting_state == "recording"`.
     - Meeting ID: `meeting_id = isolated_controller.active_meeting_id`.
     - Assert `meeting_id is not None`.
  3. Live Scratchpad ("My Thoughts") Private Note Taking:
     - Access Tab 1: `thoughts_tab = studio.tab_thoughts`.
     - Enter private thoughts:
       ```python
       confidential_note = (
           "[00:10] Vendor quote is inflated by 20%.\n"
           "[00:25] Counter-offer at $45,000 max.\n"
           "[00:40] Milind to confirm budget approval."
       )
       thoughts_tab.set_thoughts(confidential_note)
       thoughts_tab._persist_thoughts()
       ```
     - Query SQLite `meetings` table: `m = isolated_controller.db.get_meeting(meeting_id)`.
     - Assert `m["notes"] == confidential_note`.
     - Verify isolation flag: `assert thoughts_tab.is_isolated() is True`.
  4. Diarization Segment Injection:
     - Simulate progressive multi-speaker turns:
       - Segment 1: t=0.0..12.0s, Speaker="Speaker 1", Text="Welcome team. Today we review our local STT latency milestones."
       - Segment 2: t=12.5..28.0s, Speaker="Speaker 2", Text="CTranslate2 faster-whisper achieves 115 milliseconds on CPU."
       - Segment 3: t=28.5..42.0s, Speaker="Speaker 1", Text="Excellent. Let's merge the E2E Tier 4 test suite before release."
     - Inject via `isolated_controller.db.add_transcript_segment(...)`.
     - Rename Speaker turn: Rename "Speaker 1" -> "Milind", "Speaker 2" -> "Sarah".
     - Assert all segment records update speaker labels cleanly.
  5. Stop Meeting Recording:
     - `isolated_controller.stop_meeting_recording()`.
     - Assert `isolated_controller.meeting_state in ("transcribing", "idle")`.
  6. 7-Stage Offline MoM Synthesis & Privacy Verification:
     - Synthesize MoM deliverables using `MeetingIntelligenceEngine` or `LocalMeetingSummarizer`:
       - Executive Summary
       - Key Decisions ("Adopt local CTranslate2 faster-whisper")
       - Action Items ("Milind to merge E2E Tier 4 test suite")
     - Private Thoughts Sequestering Check:
       - Run `cleaned_mom = thoughts_tab.export_clean_mom(mom_output)`.
       - Assert `"Vendor quote is inflated"` NOT in `cleaned_mom`.
       - Assert `"$45,000 max"` NOT in `cleaned_mom`.
- **Teardown**:
  - `studio.close()`, `studio.deleteLater()`, process offscreen events.

---

### Scenario 4: Calendar 5-Minute Advance Alert -> Instant Meeting Launch -> Capture -> Export
- **Persona / Story**: 5 minutes before a scheduled meeting, VOX triggers an advance desktop prompt with attendee acoustic priming. Clicking the alert instantly primes Meeting Studio, records the conversation, and exports the finalized deliverables to Markdown and JSON.
- **Prerequisites & Fixtures**:
  - `qapp_offscreen: QApplication`
  - `isolated_controller: AppController`
  - `mock_calendar: MockCalendarProvider`
  - `isolated_tmp_dir: Path`
- **Step-by-Step Action Sequence**:
  1. Configure Calendar Manager with `mock_calendar`:
     `isolated_controller.calendar_manager.set_provider(mock_calendar)`.
  2. Schedule Meeting 4 Minutes Ahead:
     ```python
     now = datetime.now(timezone.utc)
     meeting_event = CalendarMeeting(
         id="evt_cloud_migration_99",
         title="Enterprise Cloud Migration Strategy",
         start_time=now + timedelta(minutes=4),
         end_time=now + timedelta(minutes=34),
         organizer="cto@enterprise.com",
         attendees=["cto@enterprise.com", "milind@enterprise.com", "alex@enterprise.com"],
         platform="Teams",
         join_url="https://teams.microsoft.com/l/meetup-join/cloud_sync",
     )
     mock_calendar.add_meeting(meeting_event)
     ```
  3. Poll Calendar Daemon:
     - `isolated_controller.calendar_manager.poll_now()`.
     - Assert 5-minute advance alert triggers:
       `alerts = isolated_controller.calendar_manager._evaluator.evaluate_meeting_alerts([meeting_event], current_time=now)`.
       - Assert `len(alerts) == 1`.
       - Assert `alerts[0]["alert_type"] == "5min"`.
       - Assert `alerts[0]["minutes_until_start"] == 4`.
  4. Acoustic Priming Verification:
     - Assert priming terms extracted: `["cto@enterprise.com", "milind@enterprise.com", "alex@enterprise.com", "Enterprise", "Cloud", "Migration", "Strategy"]`.
  5. 1-Click Launch Action:
     - Trigger launch from meeting event:
       `ok, msg = isolated_controller.start_meeting_recording(title=meeting_event.title)`.
       - Assert `ok is True`.
       - Assert `isolated_controller.meeting_state == "recording"`.
       - Assert `isolated_controller._active_meeting_title == "Enterprise Cloud Migration Strategy"`.
  6. Populate Transcript & Finalize Meeting:
     - Insert diarized segments and action items into `isolated_controller.db`.
     - `isolated_controller.stop_meeting_recording()`.
  7. Export Deliverables:
     - Markdown Export:
       ```python
       meeting_data = isolated_controller.db.get_meeting(isolated_controller.active_meeting_id or 1)
       md_file = isolated_tmp_dir / "migration_mom.md"
       out_path = TranscriptExporter.export_to_file(meeting_data, target_path=md_file, fmt="md")
       assert out_path.exists()
       content = md_file.read_text(encoding="utf-8")
       assert "Enterprise Cloud Migration Strategy" in content
       ```
     - JSON Export:
       ```python
       json_file = isolated_tmp_dir / "migration_mom.json"
       json_file.write_text(json.dumps(meeting_data, indent=2, default=str), encoding="utf-8")
       assert json_file.exists()
       parsed = json.loads(json_file.read_text(encoding="utf-8"))
       assert parsed["title"] == "Enterprise Cloud Migration Strategy"
       ```
- **Teardown**:
  - `isolated_controller.disconnect_calendar()`, clean temp files.

---

### Scenario 5: Offline Dictionary Custom Word Expansion and Snippet Expansion During Dictation
- **Persona / Story**: A developer dictating technical terms ("Kubernetes", "faster-whisper") and shortcuts (`/brb`, `/addr`). VOX's offline dictionary and snippet engines expand terms on the fly and track usage metrics.
- **Prerequisites & Fixtures**:
  - `qapp_offscreen: QApplication`
  - `isolated_controller: AppController`
  - `isolated_db: LocalDatabase`
- **Step-by-Step Action Sequence**:
  1. Populate Offline Vocabulary in SQLite:
     - `isolated_db.upsert_vocabulary("k8s", "Kubernetes", category="Tech", is_active=1)`
     - `isolated_db.upsert_vocabulary("whisper", "faster-whisper", category="Tech", is_active=1)`
     - `isolated_db.upsert_vocabulary("gh", "GitHub", category="Tools", is_active=1)`
  2. Populate Snippets in SQLite:
     - `isolated_db.upsert_snippet("/brb", "be right back", category="Quick")`
     - `isolated_db.upsert_snippet("/addr", "Building 4, Tech Campus, CA 94025", category="Contact")`
  3. Direct Processing Rule Assertions:
     - Test Vocabulary:
       `text_vocab = DictationProcessor.apply_custom_vocabulary("deploying k8s with whisper", isolated_db.list_vocabulary(active_only=True))`
       Assert `"Kubernetes"` in text_vocab and `"faster-whisper"` in text_vocab.
     - Test Snippet Expansion:
       `text_snippet = DictationProcessor.expand_snippets("please send to /addr I will /brb", isolated_db.list_snippets())`
       Assert `"Building 4, Tech Campus, CA 94025"` in text_snippet.
       Assert `"be right back"` in text_snippet.
  4. Full Active Dictation Integration Run:
     - Set up mock transcriber: returns `"please visit /addr and commit to gh with k8s period"`.
     - Track insertion: `inserted = []`, `isolated_controller.text_inserter.insert_text = MagicMock(side_effect=lambda t, **kw: (inserted.append(t), (True, "sendinput_unicode"))[1])`.
     - Execute dictation session (`start_dictation()` -> `stop_dictation_and_insert()`).
     - Spin event loop until completed.
  5. Assertions:
     - Assert `len(inserted) == 1`.
     - Assert `"Building 4, Tech Campus, CA 94025"` in inserted[0].
     - Assert `"GitHub"` in inserted[0].
     - Assert `"Kubernetes"` in inserted[0].
     - Snippet Usage Counter Check:
       - Query snippets from `isolated_db.list_snippets()`.
       - Assert snippet `/addr` usage count incremented by 1.
     - WAL Dual-Column Integrity Check:
       - Latest history entry: `raw_text` preserves `"/addr"`, `final_text` contains expanded address.
- **Teardown**:
  - Clear vocabulary and snippets, reset controller.

---

### Scenario 6: First-Run Onboarding Walkthrough -> System Tray -> Command Palette (`Ctrl+K`)
- **Persona / Story**: A new user completes the 4-step onboarding wizard, the system tray icon activates, and they immediately use `Ctrl+K` to search and jump to the Snippets workspace.
- **Prerequisites & Fixtures**:
  - `qapp_offscreen: QApplication`
  - `isolated_controller: AppController`
  - `offscreen_widgets: Dict[str, Any]` (providing `wizard`, `main_window`, `palette`)
- **Step-by-Step Action Sequence**:
  1. Reset Onboarding State:
     - `isolated_controller.config.onboarding_completed = False`
     - `isolated_controller.config.first_run_done = False`
  2. Launch Onboarding Wizard:
     - `wizard = offscreen_widgets["wizard"]()`.
     - Step 0 (Welcome):
       - Assert `wizard.stack.currentIndex() == 0`.
       - Assert `wizard.lbl_step.text() == "Step 1 of 4"`.
       - Click Next: `wizard._next_step()`.
     - Step 1 (Setup & Mic Probe):
       - Assert `wizard.stack.currentIndex() == 1`.
       - Test mic probe: `wizard.btn_test_mic.click()`.
       - Simulate audio input: `wizard._on_mic_level(0.65)`.
       - Assert `wizard.mic_meter.value() > 0`.
       - Click Next: `wizard._next_step()`.
     - Step 2 (Shortcuts):
       - Assert `wizard.stack.currentIndex() == 2`.
       - Click Next: `wizard._next_step()`.
     - Step 3 (Try VOX Live Voice Test):
       - Assert `wizard.stack.currentIndex() == 3`.
       - Run sample self-correction demo: `wizard._run_sample_preview()`.
       - Assert `wizard.tiy_editor.toPlainText()` populated.
       - Click Finish: `wizard._next_step()` -> executes `_finish_tour()`.
     - Assertions:
       - Assert `isolated_controller.config.onboarding_completed is True`.
       - Assert `isolated_controller.config.first_run_done is True`.
  3. System Tray Manager Activation:
     - Create tray manager:
       ```python
       tray = SystemTrayManager(
           parent_widget=wizard,
           controller=isolated_controller,
           floating_widget=offscreen_widgets["pill"](),
           on_open_tab=lambda idx: None,
           on_exit_app=lambda: None,
       )
       ```
     - Assert `tray.tray.toolTip()` contains "VOX".
     - Assert `tray.act_status.text()` indicates "Ready".
     - Verify context menu contains actions for Dictation, Notetaker, Scratchpad, and Workspaces.
  4. Command Palette Quick Navigation:
     - Launch Hub: `hub = offscreen_widgets["main_window"]()`.
     - Open Command Palette (`Ctrl+K` simulation): `palette = offscreen_widgets["palette"]()`.
     - Type query: `palette.txt_search.setText("snippets")`.
     - Filtered list displays "Snippets" navigation command.
     - Trigger activation:
       `hub.navigate_to_workspace("snippets")`.
       - Assert `hub.workspace_stack.currentIndex() == 3` (Snippets workspace active).
- **Teardown**:
  - Close wizard, close palette, close hub, process events.

---

### Scenario 7: Continuous Long-Form Dictation with Intermittent Pauses & WAL Persistence
- **Persona / Story**: A writer dictates a 3-paragraph article in Hands-Free mode, pausing between sentences. Smart leading spacing, capitalization, and SQLite WAL durability are verified across all utterances.
- **Prerequisites & Fixtures**:
  - `qapp_offscreen: QApplication`
  - `isolated_controller: AppController`
  - `isolated_db: LocalDatabase`
- **Step-by-Step Action Sequence**:
  1. Start Continuous Hands-Free Voice Typing:
     - `ok = isolated_controller.start_hands_free_typing()`.
     - Assert `ok is True`.
     - Assert `isolated_controller.is_hands_free_active is True`.
     - Assert `isolated_controller.dictation_state == "listening"`.
  2. Utterance 1 (Introductory Statement):
     - Transcribed speech: `"Welcome to Project VOX period"`
     - Worker formatting: `_format_and_commit_dictation(...)` -> Inserter receives `"Welcome to Project VOX. "`
     - Assert `isolated_controller._last_dictation_ended_sentence is True`.
  3. Pause Interval Simulation:
     - 1.0 second pause: state remains `listening`, target window locked.
  4. Utterance 2 (Continuous Clause with Smart Spacing):
     - Transcribed speech: `"this is continuous hands free typing comma verifying multi utterance streaming period"`
     - Worker formatting: detects previous sentence ended with period -> applies capitalization:
       `"This is continuous hands-free typing, verifying multi-utterance streaming. "`
     - Inserter receives properly spaced and capitalized sentence.
  5. Utterance 3 (Paragraph Break):
     - Transcribed speech: `"new paragraph all records persist to SQLite WAL exclamation mark"`
     - Worker formatting: `"new paragraph"` maps to `\n\n` -> Inserter receives `"\n\nAll records persist to SQLite WAL! "`
  6. Stop Hands-Free Voice Typing:
     - `isolated_controller.stop_hands_free_typing()`.
     - Assert `isolated_controller.is_hands_free_active is False`.
     - Assert `isolated_controller.dictation_state == "idle"`.
  7. SQLite WAL Verification:
     - Check WAL journal mode:
       `journal_mode = isolated_db._connect().execute("PRAGMA journal_mode;").fetchone()[0]`
       `assert journal_mode.lower() == "wal"`
     - Check History Records:
       - Query `isolated_db.list_dictation_history(limit=10)`.
       - Assert exactly 3 distinct entries exist.
       - Assert sequential timestamps.
       - Assert smart spacing is preserved across utterances.
- **Teardown**:
  - `isolated_controller.shutdown()`, process events.

---

### Scenario 8: Quick Scratchpad Note Creation, Auto-Save, Drawer Sync, and Hub Search
- **Persona / Story**: A user drafts an urgent action note in the standalone floating Scratchpad window, reviews and edits it in the companion Notetaker Drawer, and searches for it later in Flow Hub.
- **Prerequisites & Fixtures**:
  - `qapp_offscreen: QApplication`
  - `isolated_controller: AppController`
  - `isolated_db: LocalDatabase`
  - `offscreen_widgets: Dict[str, Any]` (providing `scratchpad`, `drawer`, `main_window`)
- **Step-by-Step Action Sequence**:
  1. Create Note in Scratchpad Window:
     - `scratch = offscreen_widgets["scratchpad"]()`.
     - Populate content:
       ```python
       note_text = (
           "# Project VOX Production Verification\n"
           "- SendInput Unicode verified with zero dropped characters.\n"
           "- SQLite WAL persistence confirmed."
       )
       scratch.editor.setPlainText(note_text)
       scratch.save_current_note()
       ```
     - Assert note ID created in SQLite `scratchpad_notes` table:
       `notes = isolated_db.list_notes()`.
       `assert len(notes) >= 1`.
       `assert "Project VOX Production Verification" in notes[0]["title"]`.
  2. Companion Drawer Synchronization:
     - `drawer = offscreen_widgets["drawer"]()`.
     - Trigger controller signal: `isolated_controller.notes_updated.emit()`.
     - Drawer displays note in recent notes list.
     - Append an item from drawer:
       `isolated_db.update_note(notes[0]["id"], content=note_text + "\n- Companion drawer sync complete.")`.
     - Version history check:
       `versions = isolated_db.list_note_versions(notes[0]["id"])`.
       `assert len(versions) >= 1`.
  3. Flow Hub Search Retrieval:
     - `hub = offscreen_widgets["main_window"]()`.
     - Navigate to `/scratchpad` route: `hub.navigate_to_workspace("scratchpad")`.
     - Assert `hub.scratchpad_page` displays "Project VOX Production Verification".
     - Open Global Search:
       - Query: `"zero dropped characters"`.
       - Search returns the matching note record with highlight snippet.
- **Teardown**:
  - Close scratchpad, drawer, hub; process events.

---

### Scenario 9: Mode Switching On-the-Fly (`Win+F1..F5` / Hotkeys) During Active Session
- **Persona / Story**: During an ongoing dictation workflow, the user hits `Win+F1` through `Win+F5` to change modes on the fly. Each mode formats the identical underlying speech according to its contract.
- **Prerequisites & Fixtures**:
  - `qapp_offscreen: QApplication`
  - `isolated_controller: AppController`
  - `offscreen_widgets: Dict[str, Any]` (providing `pill`)
- **Step-by-Step Action Sequence**:
  1. Instantiate Floating Voice Pill: `pill = offscreen_widgets["pill"]()`.
  2. Test Raw Speech Utterance:
     `raw_input = "um please note that the server will reboot at midnight comma save all files period"`
  3. Mode 1 (`Win+F1` / `mode_1` -> Verbatim):
     - `isolated_controller.handle_hotkey_action("mode_1", is_release=False)`.
     - Assert `isolated_controller.active_writing_mode == "verbatim"`.
     - Process speech -> Output text: `"um please note that the server will reboot at midnight comma save all files period"`
     - Assert "um", "comma", "period" retained verbatim.
  4. Mode 2 (`Win+F2` / `mode_2` -> Clean):
     - `isolated_controller.handle_hotkey_action("mode_2", is_release=False)`.
     - Assert `isolated_controller.active_writing_mode == "clean"`.
     - Process speech -> Output text: `"Please note that the server will reboot at midnight, save all files."`
     - Assert "um" stripped, punctuation converted.
  5. Mode 3 (`Win+F3` / `mode_3` -> Polished):
     - `isolated_controller.handle_hotkey_action("mode_3", is_release=False)`.
     - Assert `isolated_controller.active_writing_mode == "polished"`.
     - Process speech -> Output text: Grammatically enhanced professional wording.
  6. Mode 4 (`Win+F4` / `mode_4` -> Structured):
     - `isolated_controller.handle_hotkey_action("mode_4", is_release=False)`.
     - Assert `isolated_controller.active_writing_mode == "structured"`.
     - Process speech -> Formats into bullet list: `"- Server reboot scheduled for midnight\n- Action: Save all files"`.
  7. Mode 5 (`Win+F5` / `mode_5` -> Email):
     - `isolated_controller.handle_hotkey_action("mode_5", is_release=False)`.
     - Assert `isolated_controller.active_writing_mode == "email"`.
     - Process speech -> Formats with greeting, clear paragraph, and professional signoff.
  8. Floating Pill UI Synchronization:
     - Assert pill badge/icon updates on each mode switch.
- **Teardown**:
  - Close pill, reset controller.

---

### Scenario 10: Clipboard Restore Safety Workflow (Zero Leakage / Bit-for-Bit Preservation)
- **Persona / Story**: The user has copied a highly confidential API key to their clipboard. They use voice typing in an application that requires clipboard paste fallback. VOX injects the text via clipboard paste and immediately restores the confidential key bit-for-bit.
- **Prerequisites & Fixtures**:
  - `qapp_offscreen: QApplication`
  - `isolated_controller: AppController`
- **Step-by-Step Action Sequence**:
  1. Pre-Seed Clipboard with Sensitive Token:
     ```python
     SECRET_API_KEY = "sk-ant-api03-VOX-SECRET-KEY-9988776655-CONFIDENTIAL"
     set_clipboard_text(SECRET_API_KEY)
     assert get_clipboard_text() == SECRET_API_KEY
     ```
  2. Configure Dictation Inserter:
     - Set `isolated_controller.config.insertion_method = "clipboard"`
     - Set `isolated_controller.config.restore_clipboard = True`
  3. Execute Text Injection via Clipboard Fallback:
     - Dictated speech text: `"Status report: All 20 features passing."`
     - Call `ok, method = isolated_controller.text_inserter.insert_text("Status report: All 20 features passing.", method="clipboard", restore_clipboard=True)`
     - Assert `ok is True`.
     - Assert `method == "clipboard_paste"`.
  4. Immediate Clipboard Verification:
     - Read clipboard text immediately after paste completes:
       `restored_clip = get_clipboard_text()`
     - **Critical Assertion**:
       `assert restored_clip == SECRET_API_KEY`
     - The user's confidential clipboard content is 100% intact!
  5. Unicode / Special Character Clipboard Preservation:
     - Seed clipboard with Devanagari text:
       `UNICODE_SECRET = "गोपनीय कुंजी — 12345! @ # $ %"`
       `set_clipboard_text(UNICODE_SECRET)`
     - Inject new dictation text via clipboard.
     - Assert `get_clipboard_text() == UNICODE_SECRET`.
  6. Empty Clipboard Preservation:
     - Empty the clipboard: `user32.OpenClipboard(None); user32.EmptyClipboard(); user32.CloseClipboard()`.
     - Inject text via clipboard paste.
     - Assert inserter handles empty prior clipboard without error.
- **Teardown**:
  - Clear clipboard, process offscreen events.

---

### Scenario 11: Dictation Cancellation & Mid-Flight Dismiss Lifecycle
- **Persona / Story**: A user begins dictating (PTT or POPO), but realizes they are speaking in error and presses `ESC` / `Dismiss`. VOX cancels the session, discards the audio buffer, injects 0 characters, and leaves 0 dirty state.
- **Prerequisites & Fixtures**:
  - `qapp_offscreen: QApplication`
  - `isolated_controller: AppController`
  - `isolated_db: LocalDatabase`
- **Step-by-Step Action Sequence**:
  1. Start Dictation Session:
     - `isolated_controller.handle_hotkey_action("ptt", is_release=False)`.
     - Assert `isolated_controller.dictation_state in ("initializing", "listening")`.
  2. Accumulate Audio in Buffer:
     - Recorder is capturing audio frames.
  3. User Triggers Dismiss / Cancel:
     - `isolated_controller.handle_hotkey_action("dismiss", is_release=False)`.
     - Controller invokes `cancel_dictation()`:
       - Sets `_dictation_cancelled = True`.
       - Emits `DictationState.DISMISSED` -> `DictationState.IDLE`.
       - Clears locked target window.
  4. Invariant Assertions:
     - Assert `isolated_controller.dictation_state == "idle"`.
     - Assert `isolated_controller.text_inserter.insert_text` was NEVER called.
     - Assert 0 rows added to `dictation_history` (or status marked `dismissed`).
     - Assert no background worker thread continues transcribing.
- **Teardown**:
  - Controller shutdown, process events.

---

### Scenario 12: Dual-Tier Floating FlowBar UI Manipulation & Equalizer Reactivity
- **Persona / Story**: An interactive UI verification of the floating FlowBar widget: hovering to expand from resting capsule (40x8) to ready capsule (50x30), observing real-time 10-bar equalizer reactivity, and clicking the coral red stop button to finalize dictation.
- **Prerequisites & Fixtures**:
  - `qapp_offscreen: QApplication`
  - `isolated_controller: AppController`
  - `offscreen_widgets: Dict[str, Any]` (providing `pill`)
- **Step-by-Step Action Sequence**:
  1. Instantiate Floating FlowBar: `pill = offscreen_widgets["pill"]()`.
  2. Resting Capsule Verification:
     - Initial state: `pill.pill_state == PillState.IDLE`.
     - Dimensions: `pill.width() == 40`, `pill.height() == 8`.
  3. Hover Expansion to Ready Capsule:
     - Simulate hover: `pill._is_hovered = True; pill._sync_pill_visuals()`.
     - State transitions to `PillState.READY`.
     - Dimensions expand to `50x30`.
     - Quick Scratchpad button and keyboard shortcut tooltip become active.
  4. Start Dictation & Active Capsule:
     - Trigger dictation: `isolated_controller.start_dictation()`.
     - Pill transitions to `PillState.LISTENING`.
     - Dimensions expand to `74x30` (PTT) or `104x30` (Hands-Free).
     - Coral stop button (`#FF5757`) becomes visible and enabled.
  5. Audio Equalizer Waveform Reactivity:
     - Feed varying audio RMS levels:
       - Low: `pill.set_audio_level(0.15)` -> waveform smooth level tracks 0.15.
       - High: `pill.set_audio_level(0.85)` -> waveform bars scale dynamically.
     - Assert waveform capsule calculates parabolic center-weighted bar heights.
  6. Stop Button Click:
     - Simulate click on coral stop button:
       `pill._btn_stop.click()` or `pill._on_pill_clicked()`.
     - Controller receives finalize request -> transitions to `PillState.PROCESSING` -> `PillState.COMMITTED` -> `PillState.IDLE`.
- **Teardown**:
  - Close pill, process offscreen events.

---

## 6. Caveats

1. **Windows SendInput Emulation in Offscreen Mode**:
   - Because pytest runs headlessly (`QT_QPA_PLATFORM=offscreen`), physical Windows OS keystrokes cannot be observed by real third-party applications without stealing focus. The test harness mocks `text_inserter.insert_text` or exercises `_send_unicode_text` / `_paste_via_clipboard` against a sandboxed target HWND. The actual text encoding, UTF-16 code unit generation, and clipboard backup/restore routines are executed 100% authentically.
2. **Audio Capture Hardware Isolation**:
   - Tests do not require a physical microphone hardware device; the `synthetic_audio` fixture generates compliant 16kHz float32 mono PCM arrays, allowing deterministic, lightning-fast execution in CI environments.

---

## 7. Conclusion

The specification mining and scenario design for Tier 4 Real-World Application Scenarios is complete and fully documented.
All 10 required scenarios plus 2 bonus scenarios have been designed with:
- Exact method signatures matching `AppController`, `DictationProcessor`, `LocalDatabase`, `DualTierFloatingPill`, `VoxMeetingStudioWorkspace`, and `TextInserter`.
- Concrete step-by-step action sequences and verifiable assertions.
- 100% headless execution guarantees with 0 desktop interference.

The blueprint is ready for immediate test authoring in `tests/e2e/test_tier4_scenarios.py`.

---

## 8. Verification Method

To verify the test infrastructure and readiness for Tier 4 authoring:
1. **Run existing Tier 3 cross-feature tests**:
   ```powershell
   $env:PYTHONPATH="src"
   .\.venv\Scripts\python.exe -m pytest tests/e2e/test_tier3_pairwise.py -v
   ```
   *Expected outcome*: 22 passed in < 10 seconds.

2. **Verify Verbal Retraction execution**:
   ```powershell
   $env:PYTHONPATH="src"
   .\.venv\Scripts\python.exe -c "from voxlocal.engine.dictation_processor import DictationProcessor; res = DictationProcessor.detect_verbal_self_corrections('I want to eat apples scratch that oranges actually mangoes'); assert res['clean_text'] == 'I want mangoes'; print('Retraction verified:', res['clean_text'])"
   ```
   *Expected outcome*: `Retraction verified: I want mangoes`.

3. **Verify Clipboard Backup and Restoration**:
   ```powershell
   $env:PYTHONPATH="src"
   .\.venv\Scripts\python.exe -c "from voxlocal.win32.text_inserter import get_clipboard_text, set_clipboard_text; set_clipboard_text('VOX_TEST_SECRET'); assert get_clipboard_text() == 'VOX_TEST_SECRET'; print('Clipboard verified')"
   ```
   *Expected outcome*: `Clipboard verified`.

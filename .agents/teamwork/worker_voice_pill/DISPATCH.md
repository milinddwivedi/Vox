<h2>2026-11-02T23:50:05Z</h2>

You are worker_voice_pill, a teamwork_worker agent.
Your working directory is: d:\Milind\Voice Assistant\.agents\teamwork\worker_voice_pill/
You must first read the authoritative user request at: d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md

CRITICAL MANDATORY FORMATTING CONSTRAINT:
Under NO circumstances may ANY header begin with markdown # or *! All headers must use HTML tags (<h2>, <h3>, <h4>) or bold text across all artifacts, direct responses, and durable files. Never start any line with # or * as a header.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Context & Specifications to Read:
- `d:\Milind\Voice Assistant\VOX_PRODUCT_UX_SPECIFICATION.md`
- `d:\Milind\Voice Assistant\VOX_TARGET_ARCHITECTURE.md`
- `d:\Milind\Voice Assistant\VOX_PRODUCT_REQUIREMENTS.md`
- `d:\Milind\Voice Assistant\VOX_FEATURE_GAP_MATRIX.md`
- Reference Image 2: `C:/Users/milin/.gemini/antigravity/brain/7e9fcebc-1cf3-4086-b6f8-8191627a24f9/.user_uploaded/media_1793659390219_268052d2.png`

Exclusive File Ownership:
- `src/voxlocal/ui/floating_pill.py` (or `src/voxlocal/ui/floating_widget.py`)
- `src/voxlocal/engine/dictation_processor.py`
- `src/voxlocal/engine/streaming_typer.py`
- `src/voxlocal/core/vocabulary_manager.py` (create or enhance as needed)
- `tests/test_voice_typing_evolved.py` (new test suite)

Detailed Implementation Requirements:
1. Evolved Dual-Tier Floating Pill Control:
   - Match Reference Image 2:
     * Upper Tier: Left dismiss/cancel button ('X'), centered live dynamic 12-bar audio waveform equalizer capsule, elapsed session timer display ('mm:ss'), and right rounded red/coral stop button ('#E05638').
     * Lower Tier: Three transformation mode selector chips: Magic [Win+F1], Structured [Win+F2], Email [Win+F3] with mode selector chevron.
     * Support 5 discrete interactive states: Idle, Listening, Processing, Committed, Error.
     * Focus invariance: Preserve Win32 `WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW` so clicking or interacting with the pill never steals focus from the target application.
     * Global hotkeys: Win+F1 (Magic), Win+F2 (Structured), Win+F3 (Email), and push-to-talk toggling.
     * Live Speech Preview Bubble: Floating above the pill showing real-time streaming speech preview with visual red strikethrough styling for retracted speech/fillers.

2. Real-Time Verbal Self-Correction in `dictation_processor.py`:
   - Detect mid-utterance spoken correction markers ("no actually...", "wait sorry...", "scratch that...", "scratch last sentence...", "delete that...", "I mean...", "correction...").
   - Retract the erroneous preceding phrase/thought and substitute the corrected formulation dynamically.

3. Verbal Filler-Word Pruning in `dictation_processor.py`:
   - Prune disfluencies ("um", "uh", "you know", "like", "basically", "so basically") when in Magic, Structured, or Email modes, while preserving exact verbatim text in Verbatim mode.

4. Personal / Domain Vocabulary Manager in `src/voxlocal/core/vocabulary_manager.py`:
   - Persistent personal vocabulary dictionary with custom jargon, technical terms, acronyms, company names, and Devanagari/Hindi loanwords.
   - Hook into transcription normalization so custom terms are correctly injected.

5. Verification & Test Suite:
   - Create `tests/test_voice_typing_evolved.py` verifying:
     * Pill UI states, timer, waveform, and mode switching.
     * Verbal self-correction engine ("no actually...", "scratch that...").
     * Filler-word pruning in Magic/Structured/Email modes vs Verbatim preservation.
     * Vocabulary manager persistence and word replacement.
   - Run pytest using `.venv\Scripts\pytest.exe tests/test_voice_typing_evolved.py` and verify all tests pass 100%.
   - Ensure existing voice typing tests in `tests/` pass with zero regressions.

6. Handoff:
   - Document changes in `handoff.md` in your working directory with all 5 required handoff components (Observation, Logic Chain, Caveats, Conclusion, Verification Method).
   - Strictly comply with HTML headers (zero # or * headers).
   - Notify parent orchestrator via send_message when complete.

<h2>Progress & Liveness</h2>
Last visited: 2026-11-03T00:08:00Z
- Implemented VocabularyManager in src/voxlocal/core/vocabulary_manager.py with persistence, domain jargon, Devanagari loanwords, and longest-phrase normalization.
- Enhanced DictationProcessor in src/voxlocal/engine/dictation_processor.py with TransformationMode (Magic, Structured, Email, Verbatim, Cleaned), real-time verbal self-correction ("no actually", "wait sorry", "scratch that", "scratch last sentence", "delete that", "I mean", "correction"), annotated HTML strikethrough preview, and conversational filler pruning.
- Implemented StreamingTyper in src/voxlocal/engine/streaming_typer.py managing streaming hypothesis, self-correction retraction, and mode transformations.
- Implemented DualTierFloatingPill in src/voxlocal/ui/floating_pill.py matching Reference Image 2: Upper tier (dismiss, 12-bar equalizer, timer, coral stop button), Lower tier (Magic Win+F1, Structured Win+F2, Email Win+F3, Chevron), 5 discrete states, Win32 focus invariance, and LiveSpeechPreviewBubble.
- Created tests/test_voice_typing_evolved.py with 28 comprehensive tests (100% pass).
- Executed full test suite across 69 tests with zero regressions (69 passed in 18.58s).

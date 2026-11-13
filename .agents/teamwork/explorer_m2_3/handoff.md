# Milestone 2 Investigation Handoff Report: 5 Transformation Modes, Retraction Parser & Test Verification Suites

**Agent**: Explorer M2-3  
**Focus Area**: 5 Transformation Modes, Verbal Self-Correction Parser, Spoken Punctuation, and Test Verification Suites  
**State Folder**: `d:\Milind\Voice Assistant\.agents\teamwork\explorer_m2_3`  
**Target File**: `src/voxlocal/engine/dictation_processor.py`  
**Test Suites**:
- `tests/test_wispr_parity_features.py`
- `tests/test_voice_typing_evolved.py`
- `tests/test_voice_typing_lifecycle_and_target.py`
- `tests/test_adversarial_vox_capabilities.py`
- `tests/test_adversarial_m2_empirical.py`
- `tests/test_challenger_stress_m2.py`

---

## 1. Observation

### 1.1 Architecture & Codebase State in `dictation_processor.py`
Direct inspection of `src/voxlocal/engine/dictation_processor.py` (lines 17-26, 32-49, 593-616, 1118-1378, 1563-1715) reveals the current implementation structure:

#### A. 5 Transformation Modes
- **Enum Definition** (lines 17-25):
  ```python
  class TransformationMode(str, Enum):
      VERBATIM = "verbatim"
      CLEAN = "clean"
      CLEANED = "cleaned"
      POLISHED = "polished"
      MAGIC = "magic"
      STRUCTURED = "structured"
      EMAIL = "email"
  ```
- **Verbatim Mode**:
  - In `DictationProcessor.process()` (lines 1581-1588, 1604, 1608, 1649, 1658):
    - When `mode_str == "verbatim"`: `cleanup_enabled = False`, `spoken_punctuation = False`.
    - Snippet expansion is skipped (`if snippets and mode_str != "verbatim":`).
    - Verbal self-correction is skipped (`if cleanup_enabled:`).
    - Conversational filler pruning is bypassed: `prune_filler_words` returns input text unchanged if `mode_str == "verbatim"` (lines 1100-1102).
    - Numeric/currency formatting and Vibe Coding variable recognition are bypassed (`if mode_str != "verbatim":`, lines 1649-1655).
    - Whitespace normalization and sentence capitalization are bypassed (inside `if cleanup_enabled:`).
    - In `transform_mode()` (lines 1326-1328): returns raw `text` immediately without modification.
- **Clean Mode** (`clean` / `cleaned`):
  - Strips trailing Whisper pauses/ellipses (`...`, `…`).
  - Normalizes Devanagari or transliterates to Roman Hinglish if configured (`ensure_english_script`).
  - Expands voice snippets via `apply_snippets()`.
  - Strips verbal self-corrections via `parse_verbal_self_correction()`.
  - Prunes disfluencies and conversational fillers (`um`, `uh`, `you know`, `basically`, `so basically`, non-grammatical `like`).
  - Applies custom personal/domain vocabulary replacements (longest phrase first).
  - Normalizes built-in accent/phonetic rules (`ACCENT_CORRECTION_RULES`: 98+ rules).
  - Converts spoken punctuation commands (`SPOKEN_PUNCTUATION_RULES`).
  - Formats numbers, currencies (`$50`, `₹500`, `€100`, `£50`), percentages, versions, math equations.
  - Formats Vibe Coding symbols (snake_case, camelCase, PascalCase, kebab-case, dunder `__init__`, `@file.ext` tagging).
  - Cleans spaces before punctuation, deduplicates accidental periods/commas, capitalizes after sentence breaks.
  - Applies app context style tone (`formal`, `casual`, `very_casual`, `excited`).
- **Polished Mode** (`polished` / `magic`):
  - Runs all Clean mode formatting.
  - In `transform_mode()` (lines 1334-1343):
    ```python
    if mode_str in ("polished", "magic"):
        sentences = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", cleaned) if s.strip()]
        polished = []
        for s in sentences:
            if s and s[0].islower():
                s = s[0].upper() + s[1:]
            if s and not s[-1] in (".", "!", "?", "।"):
                s += "."
            polished.append(s)
        return " ".join(polished)
    ```
- **Structured Mode**:
  - Runs all Clean mode formatting.
  - In `transform_mode()` (lines 1345-1365):
    Splits on: `r"(?:^|\s*)(?:firstly|secondly|thirdly|first|second|third|finally|next|also|bullet|item|\d+\.|\-|\*)\s*[:,-]?\s*"`.
    If `len(raw_splits) > 1`: outputs `**Key Points & Outline:**\n` with `- {item}` bullets.
    Else if multiple sentences: outputs `**Summary:**\n` with `- {sentence}` bullets.
    Else: outputs single bullet `- {item}`.
- **Email Mode**:
  - Runs all Clean mode formatting.
  - In `transform_mode()` (lines 1366-1376):
    ```python
    if mode_str == "email":
        words = cleaned.split()
        subject_phrase = " ".join(words[:5]).rstrip(".,!?:")
        subject = f"Subject: Follow-up: {subject_phrase.capitalize()}\n\n"
        greeting = "Hi Team,\n\n"
        body = f"{cleaned[0].upper() + cleaned[1:]}\n\n"
        if not body.strip().endswith((".", "!", "?")):
            body = body.rstrip() + ".\n\n"
        cta = "Please let me know your thoughts or if you need any adjustments.\n\n"
        sign_off = "Best regards,\nVOX Assistant"
        return f"{subject}{greeting}{body}{cta}{sign_off}"
    ```

#### B. Spoken Punctuation Mapping
- Defined in `DictationProcessor.SPOKEN_PUNCTUATION_RULES` (lines 32-49):
  - `\bnew paragraph\b`, `\bnext paragraph\b` -> `\n\n`
  - `\bnew line\b`, `\bnext line\b` -> `\n`
  - `\bquestion mark\b` -> `?`
  - `\bexclamation point\b`, `\bexclamation mark\b` -> `!`
  - `\bfull stop\b`, `\bperiod\b` -> `.`
  - `\bopen parenthesis\b` -> `(`
  - `\bclose parenthesis\b` -> `)`
  - `\bopen quote\b`, `\bclose quote\b` -> `"`
  - `\bsemicolon\b` -> `;`
  - `\bcomma\b` -> `,`
  - `\bcolon\b` -> `:`
- Applied in lines 1639-1646 with spacing normalization.

#### C. Verbal Self-Correction Retraction Parser
- Defined in `CORRECTION_MARKER_PATTERN` (lines 593-616):
  ```python
  CORRECTION_MARKER_PATTERN = re.compile(
      r"(?P<sentence_marker>[,;]?\s*\b(?:scratch[\s,]+last[\s,]+sentence|delete[\s,]+last[\s,]+sentence|undo[\s,]+last[\s,]+sentence)\b[.\s]*)"
      r"|(?P<clause_marker>[,;]?\s*\b(?:"
      r"scratch[\s,]+that[\s,]+no[\s,]+actually"
      r"|no[\s,]+actually[\s,]+scratch[\s,]+that"
      r"|actually[\s,]+wait[\s,]+scratch[\s,]+that"
      r"|no[\s,]+wait[\s,]+scratch[\s,]+that"
      r"|wait[\s,]+no[\s,]+scratch[\s,]+that"
      r"|actually[\s,]+scratch[\s,]+that"
      r"|wait[\s,]+scratch[\s,]+that"
      r"|no[\s,]+scratch[\s,]+that"
      r"|no[\s,]+actually"
      r"|actually[\s,]+no"
      r"|actually[\s,]+wait"
      r"|wait[\s,]+actually"
      r"|wait[\s,]+sorry"
      r"|sorry[\s,]+wait"
      r"|scratch[\s,]+that"
      r"|delete[\s,]+that"
      r"|undo[\s,]+that"
      r")\b[,\s]*)"
      r"|(?P<term_marker>[,;]?\s*\b(?:i[\s,]+mean|i[\s,]+meant|correction)\b[,\s]*)",
      re.IGNORECASE,
  )
  ```
- Method `detect_verbal_self_corrections(text: str)` returns `Dict[str, Any]` with keys `clean_text`, `has_corrections`, `retracted_segments`, and `annotated_html`.
- HTML Crimson Strikethrough Preview:
  Lines 1290 and 1296 generate the preview tag:
  `<span style="color:#D32F2F; text-decoration:line-through; font-weight:500;">{seg}</span>`.
- Negation and False-Positive Guards (lines 1170-1178):
  - `is_negated`: matches `\b(?:don't|dont|do not|never|cannot|can't)\s*$` in prefix.
  - `is_transitive_object`: matches `^(?:file|folder|document|email|message|database|record|item|row|column|line|page|table|entry|photo|picture|image|video|recording|account|app|card|code|branch|commit)\b` in suffix.
  - `is_existential_scratch`: matches `\b(?:has|have|had|is|was|were|there|there's|found|saw|see|seen)(?:\s+[a-z]+)?\s*(?:no|a|any)?\s*$` before `scratch that`.

### 1.2 Test Verification Execution Results
Executed `.venv\Scripts\python.exe -m pytest` across all 6 test suites requested:
```powershell
.venv\Scripts\python.exe -m pytest tests/test_wispr_parity_features.py tests/test_voice_typing_evolved.py tests/test_voice_typing_lifecycle_and_target.py tests/test_adversarial_vox_capabilities.py tests/test_adversarial_m2_empirical.py tests/test_challenger_stress_m2.py -v
```
**Result**:
- `tests/test_wispr_parity_features.py`: 10 passed
- `tests/test_voice_typing_evolved.py`: 28 passed
- `tests/test_voice_typing_lifecycle_and_target.py`: 7 passed
- `tests/test_adversarial_vox_capabilities.py`: 14 passed
- `tests/test_adversarial_m2_empirical.py`: 14 passed
- `tests/test_challenger_stress_m2.py`: 16 passed
- **Total: 89 passed in 12.36s (100% pass rate, 0 errors, 0 failures)**.

Also executed `tests/test_e2e_dictation_and_modes.py`:
- **17 passed in 0.59s**.

---

## 2. Logic Chain

### 2.1 Interface Contract Discrepancy with `SCOPE.md`
1. **Observation**: `SCOPE.md` section 3 states:
   - `Signature: process_text(raw_text: str, mode: str, strip_retractions: bool=True) -> DictationProcessedResult`
   - `Retraction HTML: <span style="color: #D32F2F; text-decoration: line-through;">[retracted words]</span> [replacement words]`
2. **Observation**: Searching `src/voxlocal/` for `process_text` and `DictationProcessedResult` yielded **zero** results.
3. **Logic**: `DictationProcessor` currently has `process()`, `process_detailed()`, and `detect_verbal_self_corrections()`. However, the explicit signature specified in the milestone scope (`process_text` returning a `DictationProcessedResult` object) does not exist. While existing tests call `DictationProcessor.process(text, mode=...)`, any external caller or audit checking the exact `SCOPE.md` Interface Contract #3 will fail unless `process_text()` and `DictationProcessedResult` are exposed as a formal API.

### 2.2 Retraction Cue Parser Incompleteness
1. **Observation**: The dispatch instructions require inspecting:
   `detection of retraction cues ("scratch that", "no wait", "correction", "actually", "I mean", "never mind")`.
2. **Observation**: Inspecting `CORRECTION_MARKER_PATTERN` (lines 593-616):
   - `"scratch that"` is present.
   - `"no wait"` is present ONLY as part of compound cue `no wait scratch that` (line 599). Standalone `"no wait"` is NOT in the regex!
   - Empirical test: `DictationProcessor.detect_verbal_self_corrections('I will attend on Monday no wait Tuesday')` outputs `has_corrections = False` and clean text `'I will attend on Monday no wait Tuesday'`.
   - `"never mind"` is completely missing from the regex.
   - Empirical test: `DictationProcessor.detect_verbal_self_corrections('Send an email to Bob never mind Alice')` outputs `has_corrections = False`.
   - Standalone `"actually"` (e.g. `"Meeting is on Monday, actually Tuesday"`) is not in the regex; only `no actually`, `actually no`, and `actually wait` are included.
   - Empirical test: `DictationProcessor.detect_verbal_self_corrections('I want five apples actually six apples')` outputs `has_corrections = False`.
   - `"correction"` and `"I mean"` are present under `term_marker` and work correctly.
3. **Logic**: The retraction parser does not currently recognize standalone `"no wait"`, `"never mind"`, or standalone `"actually"`. Adding them to `CORRECTION_MARKER_PATTERN` is necessary to achieve full Wispr Flow parity.

### 2.3 Negation and False-Positive Protection Dynamics
1. **Observation**: The instructions specify verifying `negation protection ("no wait is fine", "I actually like it")`.
2. **Observation**: Testing `'No wait is fine'` and `'I actually like it'` with the current code returns `has_corrections = False` solely because standalone `"no wait"` and `"actually"` are not present in `CORRECTION_MARKER_PATTERN`.
3. **Logic**: If the Worker adds standalone `no[\s,]+wait` or `actually` into `clause_marker` without grammatical guards:
   - `'No wait is fine'`: `pre` is empty, `post` begins with verb `is`. If treated naively, it would attempt to retract prior text or corrupt the sentence.
   - `'I actually like it'`: `actually` functions as an adverb modifying the verb `like`. If matched as a clause retraction marker, it would treat `'I'` as retracted text and output `'like it'`.
   - Therefore, when supporting `"no wait"`, `"actually"`, and `"never mind"`, the Worker must implement grammatical guards:
     - Protect `"no wait"` when at utterance beginning with no preceding clause, or followed by copula/verb/preposition (`is`, `was`, `are`, `were`, `for`, `to`).
     - Protect `"actually"` when directly modifying a verb (`like`, `think`, `want`, `need`, `prefer`, `is`, `did`) or adjective/adverb (`good`, `pretty`, `fine`, `great`), reserving retraction only for discourse transitions (e.g., following a comma or clause break: `"Monday, actually Tuesday"`).
     - Protect `"never mind"` when used as a transitive imperative followed by a noun phrase (e.g., `"never mind that"`), while allowing terminal or substitution retractions.

### 2.4 Mode Transformation Edge Cases
1. **Structured Mode Ordinal Adjective Splitting**:
   - `DictationProcessor.transform_mode(text, mode="structured")` uses:
     `r"(?:^|\s*)(?:firstly|secondly|thirdly|first|second|third|finally|next|also|bullet|item|\d+\.|\-|\*)\s*[:,-]?\s*"`
   - Empirical test: `"This is our first release and we are excited."` splits into:
     ```markdown
     **Key Points & Outline:**
     - This is our
     - Release and we are excited.
     ```
   - Logic: Splitting on `\bfirst\b` anywhere in a sentence breaks common sentences containing ordinal adjectives ("first release", "first priority", "first time"). Delimiters must be anchored to sentence beginnings, list item markers, or followed by a comma/colon.
2. **Email Mode Proper Noun Lowercasing**:
   - `subject_phrase = " ".join(words[:5]).rstrip(".,!?:")` followed by `subject_phrase.capitalize()`.
   - Empirical test: `"Please review the AWS deployment for VOX"` produces:
     `Subject: Follow-up: Please review the aws deployment`
   - Logic: Python's `str.capitalize()` forces all characters after the first to lowercase, destroying acronyms (`AWS` -> `aws`, `PR` -> `pr`) and capitalized names (`Sarah` -> `sarah`). Capitalization should only apply to the initial letter: `subject_phrase[0].upper() + subject_phrase[1:]`.
3. **Polished Mode Flattening of Spoken Paragraphs**:
   - In `transform_mode(mode="polished")`:
     `re.split(r"(?<=[.!?।])\s+", cleaned)` followed by `" ".join(polished)`.
   - Empirical test: `"First paragraph period new paragraph second paragraph period"` outputs:
     `'First paragraph. Second paragraph.'`
   - Logic: Explicit spoken paragraph breaks (`\n\n`) are stripped and joined with a single space. Polished mode should preserve structural newlines (`\n\n`).

---

## 3. Caveats

1. **Audio Pipeline Independence**:
   - This investigation focused strictly on `dictation_processor.py` linguistic transformations and test suites. Hardware WASAPI capture (`dictation_recorder.py`) and fast-whisper neural transcription (`transcriber.py`) are investigated by peer explorers (Explorer M2-1 and Explorer M2-2).
2. **Backward Compatibility**:
   - 89 existing automated tests in the test matrix depend on the existing signatures and outputs of `dictation_processor.py`. Any additions (such as `process_text`, `DictationProcessedResult`, or new retraction markers) must be strictly additive and must NOT break any of the 89 passing tests.
3. **Regex Search Pos Invariants**:
   - When introducing new retraction markers into the while-loop in `detect_verbal_self_corrections()`, care must be taken with `search_pos = m.end()` on guarded matches to prevent infinite loops.

---

## 4. Conclusion

1. **Feature Maturity**: `DictationProcessor` currently has a robust baseline implementation for the 5 modes (Verbatim, Clean, Polished, Structured, Email), extensive spoken punctuation mapping, and high-precision retraction parsing with HTML crimson strikethrough preview (`#D32F2F`).
2. **Passing Test Suites**: All 6 Milestone 2 test suites execute with a 100% pass rate (89 of 89 tests passing).
3. **Four Concrete Gaps Identified**:
   - **Gap 1 (API Contract)**: Missing `DictationProcessedResult` dataclass and `DictationProcessor.process_text(raw_text, mode, strip_retractions=True)` method as specified in `SCOPE.md`.
   - **Gap 2 (Retraction Cues)**: Standalone `"no wait"`, `"never mind"`, and standalone `"actually"` are missing from `CORRECTION_MARKER_PATTERN`.
   - **Gap 3 (Negation Guards)**: Once `"no wait"` and `"actually"` are added, linguistic guards must prevent `"no wait is fine"` and `"I actually like it"` from falsely retracting.
   - **Gap 4 (Formatting Refinements)**:
     - Structured mode must not split on internal ordinal adjectives ("our first release").
     - Email mode must not lowercase acronyms in the subject line (`AWS` -> `aws`).
     - Polished mode must not flatten deliberate `\n\n` paragraph breaks.

---

## 5. Recommended Strategy for Worker

### Step 1: Implement `DictationProcessedResult` and `process_text()` in `dictation_processor.py`
Add the standard dataclass and signature adhering to `SCOPE.md`:
```python
from dataclasses import dataclass, field

@dataclass
class DictationProcessedResult:
    raw_text: str
    formatted_text: str
    mode: str
    has_retractions: bool = False
    retracted_segments: List[str] = field(default_factory=list)
    annotated_html: str = ""

@classmethod
def process_text(
    cls,
    raw_text: str,
    mode: str = "cleaned",
    strip_retractions: bool = True,
    **kwargs: Any,
) -> DictationProcessedResult:
    """Canonical M2 entrypoint returning structured DictationProcessedResult."""
    # Run retraction detection
    retraction_info = cls.detect_verbal_self_corrections(raw_text)
    
    # Process text through pipeline
    effective_raw = retraction_info["clean_text"] if strip_retractions else raw_text
    formatted = cls.process(raw_text=effective_raw, mode=mode, **kwargs)
    
    return DictationProcessedResult(
        raw_text=raw_text,
        formatted_text=formatted,
        mode=str(getattr(mode, "value", mode)).lower(),
        has_retractions=retraction_info["has_corrections"],
        retracted_segments=retraction_info["retracted_segments"],
        annotated_html=retraction_info["annotated_html"],
    )
```

### Step 2: Expand `CORRECTION_MARKER_PATTERN` with Guarded Retraction Cues
1. Add standalone cues:
   - `(?:no[\s,]+wait)`
   - `(?:never[\s,]+mind|nevermind)`
   - `(?:actually)`
2. In `detect_verbal_self_corrections()` clause marker handler, add protective guards:
   ```python
   # Guard for 'no wait is fine' / 'no wait for me'
   is_no_wait_guard = (
       bool(re.search(r"^(?:no\s+)?wait\b", matched_marker, re.IGNORECASE))
       and (
           not pre.strip()  # sentence beginning without preceding clause
           or bool(re.match(r"^(?:is|was|are|were|has|had|for|to|that's|thats)\b", post, re.IGNORECASE))
       )
   )
   
   # Guard for adverbial 'I actually like it' / 'actually pretty good'
   is_adverbial_actually = (
       bool(re.search(r"^actually\b", matched_marker, re.IGNORECASE))
       and bool(re.match(r"^(?:like|prefer|want|need|think|know|feel|see|is|are|was|were|good|bad|fine|great|pretty|super)\b", post, re.IGNORECASE))
   )
   
   # Guard for transitive 'never mind that issue'
   is_nevermind_guard = (
       bool(re.search(r"^never\s*mind\b", matched_marker, re.IGNORECASE))
       and bool(re.match(r"^(?:that|this|the|my|your|his|her|our|their)\s+[a-z]+", post, re.IGNORECASE))
       and not pre.strip()
   )
   
   if is_negated or is_transitive_object or is_existential_scratch or is_no_wait_guard or is_adverbial_actually or is_nevermind_guard:
       search_pos = m.end()
       continue
   ```

### Step 3: Refine Mode Transformations
1. **Structured Mode**:
   Anchor split pattern so `first|second|third` only matches at the start of sentence/clause or when preceded by `bullet|item` or followed by `[:,]`:
   ```python
   raw_splits = re.split(
       r"(?:^|(?<=[.!?;\n])\s*|\s+(?:bullet|item)\s+)(?:firstly|secondly|thirdly|first|second|third|finally|next|also|\d+\.|\-|\*)\s*[:,-]?\s*"
       r"|\b(?:first|second|third)\s*[:,]\s*",
       cleaned,
       flags=re.IGNORECASE,
   )
   ```
2. **Email Mode**:
   Avoid destructive `.capitalize()` on the subject line:
   ```python
   subject_cap = subject_phrase[0].upper() + subject_phrase[1:] if subject_phrase else ""
   subject = f"Subject: Follow-up: {subject_cap}\n\n"
   ```
3. **Polished Mode**:
   Preserve internal paragraph breaks:
   ```python
   paragraphs = cleaned.split("\n\n")
   polished_paras = []
   for p in paragraphs:
       sentences = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", p) if s.strip()]
       polished_sents = [s[0].upper() + s[1:] + ("." if s[-1] not in (".", "!", "?", "।") else "") for s in sentences if s]
       polished_paras.append(" ".join(polished_sents))
   return "\n\n".join(polished_paras)
   ```

### Step 4: Add New Unit Tests in a Focused Suite
Add `tests/test_dictation_processor_m2_complete.py` covering:
- `DictationProcessor.process_text()` contract & `DictationProcessedResult` attributes.
- Standalone `"no wait"` retractions (e.g. `"Monday, no wait, Tuesday"` -> `"Tuesday"`).
- Standalone `"never mind"` retractions (e.g. `"Send to Bob, never mind, to Sarah"` -> `"Send to Sarah"`).
- Terminal `"never mind"` (e.g. `"Draft message, never mind"` -> `""`).
- Negation guards: `"No wait is fine"`, `"I actually like it"`, `"Never mind that file"`.
- Structured mode with `"This is our first release"` (no fragmenting).
- Email mode with acronyms (`"AWS deployment"` -> `Subject: Follow-up: AWS deployment`).
- Polished mode with `new paragraph` preservation.

---

## 6. Verification Method

To independently verify the codebase state and findings:

1. **Verify All Retained Milestone 2 Test Suites Pass**:
   ```powershell
   .venv\Scripts\python.exe -m pytest tests/test_wispr_parity_features.py tests/test_voice_typing_evolved.py tests/test_voice_typing_lifecycle_and_target.py tests/test_adversarial_vox_capabilities.py tests/test_adversarial_m2_empirical.py tests/test_challenger_stress_m2.py tests/test_e2e_dictation_and_modes.py -v
   ```
   *Expected*: 106 passed tests with zero errors.

2. **Verify Missing Standalone Retraction Cues via One-Liner**:
   ```powershell
   .venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'src'); from voxlocal.engine.dictation_processor import DictationProcessor; print(DictationProcessor.detect_verbal_self_corrections('Monday no wait Tuesday'))"
   ```
   *Current Output*: `{'clean_text': 'Monday no wait Tuesday', 'has_corrections': False, ...}`  
   *Target Worker Output*: `{'clean_text': 'Tuesday', 'has_corrections': True, 'retracted_segments': ['Monday no wait'], ...}`

3. **Verify Negation Protection**:
   ```powershell
   .venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'src'); from voxlocal.engine.dictation_processor import DictationProcessor; print(DictationProcessor.detect_verbal_self_corrections('No wait is fine'))"
   ```
   *Expected Output*: `{'clean_text': 'No wait is fine', 'has_corrections': False, ...}`

4. **Verify Structured Mode First-in-Sentence Edge Case**:
   ```powershell
   .venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'src'); from voxlocal.engine.dictation_processor import DictationProcessor; print(DictationProcessor.transform_mode('This is our first release and we are excited.', mode='structured'))"
   ```
   *Current Output*: Fragmented into `- This is our` and `- Release and we are excited.`  
   *Target Worker Output*: Single bullet `- This is our first release and we are excited.`

5. **Invalidation Conditions**:
   - Any test failure in `tests/test_*.py`.
   - Breaking existing `DictationProcessor.process()` or `detect_verbal_self_corrections()` return types.

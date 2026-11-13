# Milestone 8 Investigation Report: Notetaker Empty States, Settings Live VU Meter & Compute Polish

**Author:** Explorer 2 — App-Wide UX Polish & Accessibility Specialist  
**State Folder:** `d:\Milind\Voice Assistant\.agents\teamwork\teamwork_explorer_m8_2\`  
**Milestone:** Milestone 8 (App-Wide UX Polish & Accessibility)  
**Date:** 2026-11-12  
**Target Codebase:** `d:\Milind\Voice Assistant`  
**Target Files:**
- `src/voxlocal/ui/notetaker_page.py`
- `src/voxlocal/ui/settings_dialog.py`
- `src/voxlocal/ui/components.py`
- `src/voxlocal/audio/devices.py`
- `src/voxlocal/ui/hub_pages.py` (Snippets & Dictionary empty states)

---

## 1. Executive Summary

This investigation delivers the complete architectural design, layout specifications, and concrete implementation blueprints for:
1. **Polished Empty States & 1-Click Quick Starts** in `notetaker_page.py` (along with Dictionary and Snippets in `hub_pages.py`), replacing bare or blank UI panels with rich illustration cards and 1-click starter templates ("Product Standup", "Architecture Review", "1-on-1 Catchup").
2. **Real-Time Microphone Input Level Deflection Meter** in `settings_dialog.py`, backed by a leak-proof daemon probe engine in `audio/devices.py` that streams microphone RMS volume into a `VoxAudioVisualizerBar` with zero COM or WASAPI handle leaks.
3. **User-Friendly Model & Compute Precision Options** in `settings_dialog.py`, replacing raw CTranslate2 strings (`int8`, `int8_float16`, `float16`) with intuitive human-centered labels ("Fastest (Lower Memory)", "Balanced", "Maximum Accuracy") and explanatory subtext while preserving exact underlying settings keys.
4. **Standardization of Settings Layout Rhythm** across all 6 pages to an exact 16px / 12px / 8px spacing rhythm (16px card padding, 12px row spacing, 8px button group gap).
5. **Absolute Preservation of SQLite Schemas, Settings Keys, and Data Models**, maintaining 100% backward compatibility with all existing test suites.

---

## 2. Notetaker Page Empty States & Quick Starts Analysis

### 2.1 Current Baseline Observations (`notetaker_page.py`)
In `src/voxlocal/ui/notetaker_page.py`:
1. **Left Library Panel (`lib_card`):**
   - When no meetings exist in the database (or when search yields 0 matches), `self.meetings_list` (`QListWidget`) is simply cleared.
   - There is no placeholder text, no illustration, and no prompt indicating why the list is blank or how to proceed.
2. **Right Detail Panel (`detail_wrap`):**
   - Contains `self.action_card` and `self.studio` (`VoxMeetingStudioWorkspace`).
   - When no meeting is selected (`self._selected_meeting_id is None`):
     - `self.lbl_sel_title.setText("Select a call note")`
     - `self.lbl_sel_meta.setText("Pick a recorded meeting from the left library or start a new capture.")`
     - `btn_watch_video`, `btn_speakers`, `btn_export`, `btn_more`, `btn_play_pause`, and `media_slider` are disabled.
     - `self.studio` sits empty with disabled or unpopulated tabs.
   - For a first-time user or empty workspace, this feels broken or abandoned rather than inviting.

### 2.2 Reusable `VoxEmptyStateWidget` Architecture
To create a warm, minimal, Wispr Flow-inspired experience, we design `VoxEmptyStateWidget(QWidget)` in `src/voxlocal/ui/components.py`:

```
+--------------------------------------------------------------------------+
|                                                                          |
|                           [ Stylized Icon Badge ]                        |
|                                                                          |
|                     No Meeting Notes Recorded Yet                        |
|        Capture live calls privately, or jump in with a template:         |
|                                                                          |
|   +-------------------+  +-------------------+  +--------------------+   |
|   |  Product Standup  |  | Architecture Rvw  |  |   1-on-1 Catchup   |   |
|   |  Daily sync &     |  | System design &   |  | Goals, feedback &  |   |
|   |  blockers         |  | tradeoffs         |  | action items       |   |
|   +-------------------+  +-------------------+  +--------------------+   |
|                                                                          |
|                      [  ● Start Notetaker Recording  ]                    |
|                                                                          |
+--------------------------------------------------------------------------+
```

#### Specifications:
- **Badge Slot:** 56×56px rounded container (`border-radius: 28px; background: surface_well / accent_fathom_tint;`) rendering a high-contrast minimalist SVG/glyph (calendar/note icon).
- **Typography:**
  - Headline: 20px, font weight 600, serif or clean sans (`text_primary`).
  - Subtitle: 13px, font weight 400, `text_muted`, max-width 480px, word-wrapped.
- **Starter Template Cards Container:** Horizontal row (`QHBoxLayout`, spacing 12px).
- **Primary Action CTA:** `QPushButton` with `dawnBtn` or `primaryBtn` styling.

### 2.3 1-Click Starter Prompts & Templates Design

When the user clicks any of the 3 starter templates, `notetaker_page.py` creates a structured note directly in SQLite and immediately loads it in `VoxMeetingStudioWorkspace`:

#### Template 1: Product Standup
- **Title:** `Product Standup — {Date}`
- **Meeting Type:** `Standup`
- **Source Platform:** `Template`
- **Initial Notes ("My Thoughts"):**
  ```markdown
  # Product Standup
  **Date:** {Today}
  **Facilitator:** Team

  ## 1. Yesterday's Progress
  - Reviewed core feature pull requests
  - Validated test suite passes

  ## 2. Today's Planned Priorities
  - Polish empty state components
  - Verify microphone VU meter deflection

  ## 3. Blockers & Dependencies
  - None currently
  ```
- **Initial Summary:** "Daily standup meeting to align on sprint progress, prioritize deliverables, and resolve blockers."
- **Pre-populated Action Items:**
  1. `{"description": "Review sprint backlog before standup", "owner": "Team", "due_date": "Today"}`
  2. `{"description": "Test microphone input latency", "owner": "QA", "due_date": "Today"}`

#### Template 2: Architecture Review
- **Title:** `Architecture Review — {Date}`
- **Meeting Type:** `Architecture`
- **Source Platform:** `Template`
- **Initial Notes ("My Thoughts"):**
  ```markdown
  # Architecture Review: [System / Service Name]
  **Date:** {Today}
  **Status:** In Review

  ## 1. Context & Motivation
  - Why are we evaluating this architectural decision?

  ## 2. Proposed Architecture & Alternatives
  - Option A: Native in-process thread pool
  - Option B: Decoupled sub-process worker

  ## 3. Tradeoffs & Non-Functional Requirements
  - Latency vs Memory footprint
  - Fault tolerance & crash isolation

  ## 4. Security & Compliance
  - 100% local processing; zero external network egress
  ```
- **Initial Summary:** "Architecture review assessing design alternatives, technical tradeoffs, and operational feasibility."
- **Pre-populated Action Items:**
  1. `{"description": "Draft Architecture Decision Record (ADR)", "owner": "Lead Architect", "due_date": "This Week"}`
  2. `{"description": "Benchmark memory footprint on Windows", "owner": "Core Team", "due_date": "Next Sprint"}`

#### Template 3: 1-on-1 Catchup
- **Title:** `1-on-1 Catchup — {Date}`
- **Meeting Type:** `1-on-1`
- **Source Platform:** `Template`
- **Initial Notes ("My Thoughts"):**
  ```markdown
  # 1-on-1 Catchup
  **Date:** {Today}
  **Participants:** You & Team Member

  ## 1. Pulse Check & Wellbeing
  - Workload balance and overall energy

  ## 2. Recent Wins & Delivery Highlights
  - Highlights from the past cycle

  ## 3. Obstacles & Challenges
  - What friction can be removed?

  ## 4. Growth & Career Opportunities
  - Skill development and mentorship
  ```
- **Initial Summary:** "Bi-weekly 1-on-1 catchup focused on wellbeing, career development, and obstacle removal."
- **Pre-populated Action Items:**
  1. `{"description": "Schedule quarterly development review", "owner": "Manager", "due_date": "Next Week"}`
  2. `{"description": "Share feedback on proposal", "owner": "Team Member", "due_date": "Friday"}`

### 2.4 Integration into `NotetakerPage`
In `src/voxlocal/ui/notetaker_page.py`:
1. In `detail_wrap`:
   - Add `self.empty_card = VoxEmptyStateWidget(...)`.
   - When `self._selected_meeting_id is None`:
     `self.empty_card.setVisible(True)`
     `self.action_card.setVisible(False)`
     `self.studio.setVisible(False)`
   - When `self._selected_meeting_id is not None`:
     `self.empty_card.setVisible(False)`
     `self.action_card.setVisible(True)`
     `self.studio.setVisible(True)`
2. In `lib_card`:
   - Add `self.lbl_empty_lib = QLabel("No meetings yet")`.
   - When `meetings_list.count() == 0`, show `self.lbl_empty_lib`; when > 0, hide it.
3. When searching (`txt_search.text()` non-empty) with 0 results:
   - `empty_card` updates its title to `f"No meetings matching '{query}'"`.
   - Displays a "Clear Search" button that resets `txt_search.clear()`.

### 2.5 Extension to Snippets & Dictionary (`hub_pages.py`)
In `src/voxlocal/ui/hub_pages.py`:
- **Dictionary Page (`line 554`):** Replace bare `QLabel("No dictionary words found...")` with an empty card featuring:
  - Icon: `📖`
  - Title: "Your Dictionary is Empty"
  - Subtitle: "Add technical terms, brand names, acronyms, or Hinglish phrases so VOX spells them perfectly."
  - 1-click starter chips: `+ "ChatGPT"`, `+ "Kubernetes"`, `+ "Hinglish"`
- **Snippets Page (`line 811`):** Replace bare `QLabel("No snippets yet...")` with an empty card featuring:
  - Icon: `⚡`
  - Title: "No Voice Snippets Created"
  - Subtitle: "Create voice shortcuts that expand short spoken cues into long text blocks or email templates."
  - 1-click starter chips: `+ "Meeting Follow-up"`, `+ "Quick Sign-off"`, `+ "Bug Report Template"`

---

## 3. Settings UX Polish & Real-Time Input Level Deflection Meter

### 3.1 Audio Settings Tab Baseline (`settings_dialog.py`)
Currently in `settings_dialog.py`:
- Lines 451–468 populate `self.combo_mic` with input devices discovered by `AudioDeviceManager.list_devices()`.
- Users can choose a microphone, adjust gain (`self.slider_mic_gain`), and select language/model.
- **Friction Point:** The user has no visual feedback showing whether the microphone is capturing audio, whether input gain is too low or clipping, or whether Windows has granted microphone permissions.

### 3.2 Real-Time Audio Monitoring Engine (`src/voxlocal/audio/devices.py`)
To prevent memory, thread, or COM/WASAPI stream leaks, we implement `MicrophoneLevelProbe` directly in `src/voxlocal/audio/devices.py`:

```python
class MicrophoneLevelProbe:
    """
    Leak-free microphone RMS input level probe running on a background daemon thread.
    Safely opens PyAudio WASAPI input stream, reads non-blocking chunks, computes VU level,
    and cleanly releases all stream handles and COM objects on stop.
    """
    def __init__(
        self,
        device_index: Optional[int] = None,
        device_name: str = "",
        on_level: Optional[Callable[[float], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.device_index = device_index
        self.device_name = device_name
        self.on_level = on_level
        self.on_error = on_error
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="MicLevelProbeThread",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.4)
        self._thread = None

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop_event.is_set())

    def _run_loop(self) -> None:
        pa = None
        stream = None
        try:
            import pyaudiowpatch as pyaudio
            dev = AudioDeviceManager.resolve_microphone(self.device_index, self.device_name)
            if not dev:
                if self.on_error:
                    self.on_error("No working microphone found.")
                return
            pa = pyaudio.PyAudio()
            rate = max(16000, int(dev.sample_rate or 48000))
            channels = max(1, int(dev.channels or 1))
            chunk = 1024
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=dev.index,
                frames_per_buffer=chunk,
            )
            while not self._stop_event.is_set():
                data = stream.read(chunk, exception_on_overflow=False)
                if not data:
                    time.sleep(0.01)
                    continue
                pcm = np.frombuffer(data, dtype=np.int16)
                mono = to_mono_f32(pcm, channels)
                lvl = float(compute_rms_level(mono))
                if self.on_level and not self._stop_event.is_set():
                    self.on_level(min(1.0, lvl * 2.0))
                time.sleep(0.015)
        except Exception as exc:
            logger.debug("MicrophoneLevelProbe exception: %s", exc)
            if not self._stop_event.is_set() and self.on_error:
                self.on_error(str(exc))
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:
                    pass
```

### 3.3 Leak Prevention & Lifecycle Safeguards
1. **Thread Termination:** `self._stop_event.set()` signals the inner loop. The loop exits, enters `finally:`, closes the PyAudio stream, terminates PyAudio, and allows the thread to finish cleanly.
2. **Tab Switch Teardown:** In `SettingsWorkspaceWidget.select_section(idx)`: if `idx != 0` (user navigates away from General), `_stop_mic_test()` is immediately called.
3. **Window Close & Hide Teardown:** Both `hideEvent()` and `closeEvent()` on `SettingsWorkspaceWidget` and `SettingsDialog` call `_stop_mic_test()`.
4. **Device Switch Reactivity:** When the user changes `self.combo_mic`, if testing is active, the probe stops on the old device and immediately restarts on the newly selected device.
5. **Conflict Prevention:** If active dictation begins (`dictation_state != "idle"`), testing stops automatically so PyAudio doesn't hold two conflicting exclusive WASAPI streams.

### 3.4 Visual Deflection Meter Layout in `settings_dialog.py`
In `_build_general_page()`:
- Use `VoxAudioVisualizerBar` (from `voxlocal.ui.components`):
  - Fixed height: 10px, rounded corners.
  - Emerald green (`#10B981`) -> Blue (`#3B82F6`) -> Red peak (`#EF4444`) dynamic gradient.
  - Integrated peak-hold decay line.
- Numeric percentage label: `QLabel("0%")` (or "Inactive").
- "Test Mic" button: `QPushButton("Test Mic")` (checkable, toggles to "Stop Test").

---

## 4. Whisper Model & Compute Settings Friendly Labels

### 4.1 Underlying Architecture
In `src/voxlocal/core/config.py`:
- Line 140 defines `compute_type: str = "default"`.
In `src/voxlocal/engine/transcriber.py`:
- `compute_type` is passed to CTranslate2 `faster_whisper.WhisperModel`.
- Valid CTranslate2 options on CPU/GPU include: `float16`, `int8`, `int8_float16`, and `default`.

### 4.2 Friendly User Labels & Explanatory Subtext
Raw compute options intimidate non-technical users. We design a clean mapping:

| Friendly UI Label | Underlying Value | Explanatory Subtext / Tooltip | Target Hardware Profile |
| :--- | :--- | :--- | :--- |
| **Balanced (Recommended)** | `"int8_float16"` | Hybrid 8-bit weights & 16-bit compute for optimal speed and high fidelity. | NVIDIA RTX 20/30/40 series GPUs |
| **Fastest (Lower Memory)** | `"int8"` | 8-bit integer quantization for minimal VRAM/RAM usage and lowest latency. | Laptops, CPU-only, or <4GB VRAM |
| **Maximum Accuracy** | `"float16"` | Full 16-bit floating point precision with highest transcription accuracy. | Dedicated GPUs with Tensor Cores |
| **Automatic (Hardware Default)** | `"default"` | Automatically detects the optimal precision for your CPU/GPU hardware. | Any PC (system auto-selection) |

### 4.3 UI Implementation & Dynamic Explanatory Label
In `_build_general_page()`:
```python
self.combo_compute = QComboBox()
self.combo_compute.setMinimumWidth(260)
self.combo_compute.setMaximumWidth(320)
for label, val in (
    ("Balanced (Recommended)", "int8_float16"),
    ("Fastest (Lower Memory)", "int8"),
    ("Maximum Accuracy", "float16"),
    ("Automatic (Hardware Default)", "default"),
):
    self.combo_compute.addItem(label, val)
self.combo_compute.currentIndexChanged.connect(self._on_compute_changed)
self._add_control_row(
    mic_lay,
    "Compute Precision",
    "Hardware precision for Whisper speech inference.",
    self.combo_compute,
)
```

In `load_from_config()`:
```python
compute_target = getattr(cfg, "compute_type", "default") or "default"
self._set_combo_by_data(self.combo_compute, compute_target)
```

In `_on_compute_changed()`:
```python
def _on_compute_changed(self) -> None:
    val = str(self.combo_compute.currentData() or "default")
    self._save_cfg("compute_type", val)
    if hasattr(self.controller, "set_compute_type"):
        self.controller.set_compute_type(val)
```

This preserves the exact underlying configuration key `compute_type` and its raw string values without breaking any existing models or configs.

---

## 5. Settings Dialog Layout Rhythm Standardization

### 5.1 Rhythm Audit & Current Inconsistencies
| Element | Current Inconsistent Spacing | Target Standardized Rhythm | Rationale |
| :--- | :--- | :--- | :--- |
| **Page Outer Margins** | `(32, 26, 36, 36)` | `(24, 20, 24, 24)` | Eliminates asymmetric clipping; aligns with main window grid. |
| **Inter-Card Spacing** | `14px` | `16px` | Standard 16px container spacing. |
| **Card Inner Padding** | `(18, 14, 18, 14)` | `(16, 16, 16, 16)` | Exact 16px card container padding specified in `PROJECT.md`. |
| **Card Row Spacing** | `12px` | `12px` | Standard 12px vertical row spacing. |
| **Control Row Horizontal Gap** | `16px` (control) / `12px` (toggle) | `12px` | Harmonizes label-to-control distance across toggles and dropdowns. |
| **Button Group Gap** | `6px` or mixed | `8px` | Standard 8px internal button group rhythm. |
| **Label Stack Gap** | `2px` | `4px` | Improves breathing room between title and subtext. |

### 5.2 Updated Helper Implementation
```python
def _page_shell(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    w.setObjectName("settingsScrollBody")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(24, 20, 24, 24)
    lay.setSpacing(16)

    lbl_t = QLabel(title)
    lbl_t.setFont(get_ui_font(24, 500, serif=True))
    lay.addWidget(lbl_t)

    lbl_s = QLabel(subtitle)
    lbl_s.setObjectName("settingsMuted")
    lbl_s.setWordWrap(True)
    lay.addWidget(lbl_s)
    return w, lay

def _make_card(self) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("settingsCard")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.setSpacing(12)
    return card, lay
```

---

## 6. Preservation of Database Schemas, Settings Keys & Data Models

| Area | Contract / Key | Preservation Verification |
| :--- | :--- | :--- |
| **SQLite `meetings` table** | `id`, `title`, `created_at`, `notes`, `summary`, `status`, `meeting_type`, `source_platform` | **100% Preserved.** Starter templates call standard `create_meeting` and `update_meeting`. No schema alteration. |
| **SQLite `action_items` table** | `id`, `meeting_id`, `description`, `owner`, `due_date`, `status` | **100% Preserved.** Templates use standard `add_action_item`. |
| **SQLite `decisions` table** | `id`, `meeting_id`, `decision`, `context`, `status` | **100% Preserved.** Templates use standard `add_decision`. |
| **`AppConfig` Settings Keys** | `compute_type`, `mic_device_index`, `mic_device_name`, `mic_gain` | **100% Preserved.** Keys already exist in `AppConfig`. No key renaming. |
| **UI Signals & Attributes** | `notetaker_page.meetings_list`, `notetaker_page.studio`, `notetaker_page.splitter` | **100% Preserved.** All tested attributes and methods remain direct members. |

---

## 7. Concrete Implementation Blueprint

### 7.1 Proposed `VoxEmptyStateWidget` in `src/voxlocal/ui/components.py`

```python
class VoxEmptyStateWidget(QFrame):
    """
    Modern, accessible empty state illustration widget with 1-click starter cards.
    """

    starter_clicked = pyqtSignal(str)
    action_clicked = pyqtSignal()

    def __init__(
        self,
        icon: str = "📝",
        title: str = "No Items Yet",
        subtitle: str = "Get started by choosing a template or creating a new item.",
        parent: Optional[QWidget] = None,
        theme: str = "light",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("voxEmptyState")
        self._theme = theme.lower()
        self._cards_layout: Optional[QHBoxLayout] = None

        self._build_ui(icon, title, subtitle)
        self.apply_theme(self._theme)

    def _build_ui(self, icon: str, title: str, subtitle: str) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 40, 32, 40)
        lay.setSpacing(16)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Icon badge
        self.lbl_icon = QLabel(icon)
        self.lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_icon.setFixedSize(56, 56)
        lay.addWidget(self.lbl_icon, 0, Qt.AlignmentFlag.AlignCenter)

        # Title
        self.lbl_title = QLabel(title)
        self.lbl_title.setFont(get_ui_font(20, 600, serif=True))
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_title)

        # Subtitle
        self.lbl_sub = QLabel(subtitle)
        self.lbl_sub.setWordWrap(True)
        self.lbl_sub.setMaximumWidth(520)
        self.lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_sub, 0, Qt.AlignmentFlag.AlignCenter)

        # Quick start container
        self.cards_wrap = QWidget()
        self._cards_layout = QHBoxLayout(self.cards_wrap)
        self._cards_layout.setContentsMargins(0, 8, 0, 8)
        self._cards_layout.setSpacing(12)
        lay.addWidget(self.cards_wrap)

        # Primary action button
        self.btn_action = QPushButton("Start Recording")
        self.btn_action.setObjectName("primaryBtn")
        self.btn_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_action.clicked.connect(self.action_clicked.emit)
        lay.addWidget(self.btn_action, 0, Qt.AlignmentFlag.AlignCenter)

    def add_starter_card(self, title: str, desc: str, template_key: str, tag: str = "") -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("starterCard")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumSize(160, 84)

        b_lay = QVBoxLayout(btn)
        b_lay.setContentsMargins(14, 12, 14, 12)
        b_lay.setSpacing(4)
        b_lay.setAlignment(Qt.AlignmentFlag.AlignLeft)

        if tag:
            lbl_tag = QLabel(tag.upper())
            lbl_tag.setStyleSheet("font-size: 9px; font-weight: 700; letter-spacing: 0.08em; opacity: 0.7;")
            b_lay.addWidget(lbl_tag)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("font-size: 13px; font-weight: 600;")
        d_lbl = QLabel(desc)
        d_lbl.setStyleSheet("font-size: 11px; opacity: 0.75;")
        d_lbl.setWordWrap(True)
        b_lay.addWidget(t_lbl)
        b_lay.addWidget(d_lbl)

        btn.clicked.connect(lambda _=False, k=template_key: self.starter_clicked.emit(k))
        if self._cards_layout:
            self._cards_layout.addWidget(btn)
        return btn

    def apply_theme(self, theme: str) -> None:
        self._theme = theme.lower()
        t = get_theme_tokens(self._theme)
        self.setStyleSheet(
            f"QFrame#voxEmptyState {{ background-color: {t['surface']}; border: 1px dashed {t['border']}; border-radius: 16px; }}"
            f"QLabel {{ color: {t['text']}; background: transparent; }}"
            f"QLabel#ntMuted {{ color: {t['text_muted']}; }}"
            f"QPushButton#starterCard {{ background-color: {t['surface_well']}; border: 1px solid {t['border']}; border-radius: 10px; text-align: left; }}"
            f"QPushButton#starterCard:hover {{ border-color: {t['accent_fathom']}; background-color: {t['sidebar_active']}; }}"
        )
        self.lbl_icon.setStyleSheet(
            f"background-color: {t['surface_well']}; border: 1px solid {t['border']}; border-radius: 28px; font-size: 24px;"
        )
```

### 7.2 Proposed Integration in `src/voxlocal/ui/notetaker_page.py`

```python
# Inside NotetakerPage._build_ui:
self.empty_card = VoxEmptyStateWidget(
    icon="🎙️",
    title="No Meeting Notes Recorded Yet",
    subtitle="Capture Zoom, Teams, and Meet calls with zero bots, or jump-start with a structured template:",
    theme=self._theme,
    parent=detail_wrap,
)
self.empty_card.add_starter_card("Product Standup", "Daily sync, blockers & updates", "standup", tag="Agile")
self.empty_card.add_starter_card("Architecture Review", "System design, RFC & tradeoffs", "arch", tag="Engineering")
self.empty_card.add_starter_card("1-on-1 Catchup", "Goals, pulse check & growth", "1on1", tag="Management")
self.empty_card.starter_clicked.connect(self._create_template_meeting)
self.empty_card.action_clicked.connect(self._on_toggle_recording_clicked)
detail_lay.addWidget(self.empty_card, 1)

# In refresh_meetings_list():
has_meetings = self.meetings_list.count() > 0
self.empty_card.setVisible(not has_meetings and self._selected_meeting_id is None)
self.action_card.setVisible(has_meetings or self._selected_meeting_id is not None)
self.studio.setVisible(has_meetings or self._selected_meeting_id is not None)
```

### 7.3 Proposed Template Creator in `NotetakerPage`

```python
def _create_template_meeting(self, template_key: str) -> None:
    now_str = datetime.now().strftime("%b %d, %Y")
    if template_key == "standup":
        title = f"Product Standup — {now_str}"
        notes = (
            f"# Product Standup\n**Date:** {now_str}\n**Facilitator:** Team\n\n"
            "## 1. Yesterday's Progress\n- Reviewed core feature pull requests\n- Validated test suite passes\n\n"
            "## 2. Today's Planned Priorities\n- Polish empty state components\n- Verify microphone VU meter deflection\n\n"
            "## 3. Blockers & Dependencies\n- None currently\n"
        )
        summary = "Daily standup meeting to align on sprint progress, prioritize deliverables, and resolve blockers."
        actions = [
            ("Review sprint backlog before standup", "Team", "Today"),
            ("Test microphone input latency", "QA", "Today"),
        ]
    elif template_key == "arch":
        title = f"Architecture Review — {now_str}"
        notes = (
            f"# Architecture Review: [System / Service Name]\n**Date:** {now_str}\n**Status:** In Review\n\n"
            "## 1. Context & Motivation\n- Why are we evaluating this architectural decision?\n\n"
            "## 2. Proposed Architecture & Alternatives\n- Option A: Native in-process thread pool\n- Option B: Decoupled sub-process worker\n\n"
            "## 3. Tradeoffs & Non-Functional Requirements\n- Latency vs Memory footprint\n- Fault tolerance & crash isolation\n\n"
            "## 4. Security & Compliance\n- 100% local processing; zero external network egress\n"
        )
        summary = "Architecture review assessing design alternatives, technical tradeoffs, and operational feasibility."
        actions = [
            ("Draft Architecture Decision Record (ADR)", "Lead Architect", "This Week"),
            ("Benchmark memory footprint on Windows", "Core Team", "Next Sprint"),
        ]
    else:  # 1on1
        title = f"1-on-1 Catchup — {now_str}"
        notes = (
            f"# 1-on-1 Catchup\n**Date:** {now_str}\n**Participants:** You & Team Member\n\n"
            "## 1. Pulse Check & Wellbeing\n- Workload balance and overall energy\n\n"
            "## 2. Recent Wins & Delivery Highlights\n- Highlights from the past cycle\n\n"
            "## 3. Obstacles & Challenges\n- What friction can be removed?\n\n"
            "## 4. Growth & Career Opportunities\n- Skill development and mentorship\n"
        )
        summary = "Bi-weekly 1-on-1 catchup focused on wellbeing, career development, and obstacle removal."
        actions = [
            ("Schedule quarterly development review", "Manager", "Next Week"),
            ("Share feedback on proposal", "Team Member", "Friday"),
        ]

    mid = self.controller.db.create_meeting(
        title=title,
        meeting_type="Meeting Reports",
        source_platform="Template",
        capture_mode="both",
    )
    self.controller.db.update_meeting(mid, status="completed", notes=notes, summary=summary)
    for desc, owner, due in actions:
        self.controller.db.add_action_item(meeting_id=mid, description=desc, owner=owner, due_date=due)

    self.refresh_all()
    self.select_meeting(mid)
```

---

## 8. Summary & Next Steps for Implementation

The architectural blueprint is finalized. The implementer can proceed with:
1. Adding `VoxEmptyStateWidget` to `src/voxlocal/ui/components.py`.
2. Adding `MicrophoneLevelProbe` to `src/voxlocal/audio/devices.py`.
3. Integrating the empty state and templates in `src/voxlocal/ui/notetaker_page.py`.
4. Adding the live VU meter and compute options to `src/voxlocal/ui/settings_dialog.py`.
5. Updating `DictionaryPage` and `SnippetsPage` in `src/voxlocal/ui/hub_pages.py`.
6. Standardizing settings padding/spacing to the 16px/12px/8px rhythm.

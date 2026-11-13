# Comprehensive Investigation Report: Centralized Semantic Design Tokens & App-Wide UX Polish

**Milestone:** 8 (Comprehensive App-Wide UX Polish & Accessibility)  
**Author:** Explorer 1 — Design Tokens, Theming & UX Architecture Specialist  
**Target:** Elimination of Hardcoded Hex Codes & Centralization of Semantic Tokens  
**Date:** 2026-11-12  
**Target Files:** `src/voxlocal/ui/theme.py`, `src/voxlocal/ui/main_window.py`, `src/voxlocal/ui/settings_dialog.py`, `src/voxlocal/ui/notetaker_page.py`, `src/voxlocal/ui/command_palette.py`, `src/voxlocal/ui/floating_pill.py`, `src/voxlocal/ui/components.py`, `src/voxlocal/ui/meeting_studio.py`, `src/voxlocal/ui/animations.py`

---

## 1. Executive Summary

A forensic audit of the VOX presentation layer revealed significant visual fragmentation and architectural leakage:
1. **Proliferation of Hardcoded Hex Values**: Over **240** direct hex color instances exist outside of `theme.py` across `src/voxlocal/ui/`. These include disparate palettes such as Tailwind CSS Slate (`#1E293B`, `#334155`, `#94A3B8`, `#F8FAFC`, `#0F172A`), GitHub Dark (`#161B22`, `#12161D`), VSCode Blue (`#3B82F6`), and hardcoded accent greens/reds (`#10B981`, `#FF5757`, `#EF4444`, `#F59E0B`).
2. **Missing Core Semantic Tokens**: Despite Wispr Flow's minimal, obsidian-pill desktop design, `theme.py` lacked explicit semantic tokens for floating surfaces (`surface_pill`, `border_pill`, `text_pill_primary`, `text_pill_muted`), canonical status accents (`accent_emerald`, `accent_coral`), container surfaces (`card_background`), and CTA states (`split_button_fill`).
3. **Inconsistent Container Padding & Spacing**: Component margins vary wildly across views (from 10px to 22px), preventing visual cohesion.
4. **Accessibility Deficits**: Interactive elements across the primary shell, command palette, and settings dialog lack screen-reader accessibility metadata (`setAccessibleName` / `setAccessibleDescription`).

This report provides the complete specification of required centralized tokens, layout constants, and an exact file-by-file refactoring plan to achieve 100% token centralization while guaranteeing zero regression of existing unit and UI tests.

---

## 2. Existing Theme Architecture (`src/voxlocal/ui/theme.py`)

### 2.1 Current Implementation Overview
`theme.py` currently defines two primary dictionaries:
- `LIGHT_TOKENS: Dict[str, str]` (Wispr Flow Sand Light palette, base `#FCFCFB`, surfaces `#FFFFFF` / `#FAF9F7`)
- `DARK_TOKENS: Dict[str, str]` (Wispr Flow Vast Dark palette, base `#1A1A1A`, surfaces `#202020` / `#262625`)

It provides helper functions:
- `get_theme_tokens(theme="light") -> Dict[str, str]`
- `get_ui_font(size_px=14, weight=400, serif=False, mono=False) -> QFont`
- `get_theme_palette(theme="light") -> QPalette`
- `get_theme_stylesheet(theme="light") -> str`
- `get_floating_pill_stylesheet(theme="light") -> str`
- `get_dialog_stylesheet(theme="light") -> str`
- `create_info_badge(tooltip: str) -> QLabel`
- `create_app_pixmap(state="idle", size=64, theme="light") -> QPixmap`
- `create_app_icon(state="idle", size=64, theme="light") -> QIcon`

### 2.2 Test Invariants & Backward Compatibility Constraints
Tests in `tests/test_theme_and_animations.py` and `tests/test_e2e_product_ux_and_architecture.py` assert strict dictionary keys:
```python
# test_theme_and_animations.py:80-88
dark_tokens = get_theme_tokens("dark")
self.assertEqual(dark_tokens["bg"], "#1A1A1A")
self.assertEqual(dark_tokens["surface"], "#202020")
self.assertEqual(dark_tokens["text"], "#E9E8E1")
light_tokens = get_theme_tokens("light")
self.assertEqual(light_tokens["bg"], "#FCFCFB")
self.assertEqual(light_tokens["surface"], "#FFFFFF")
self.assertEqual(light_tokens["text"], "#1A1A1A")

# test_e2e_product_ux_and_architecture.py:231-243
self.assertEqual(dark_tokens["background"], "#1A1A1A")
self.assertEqual(dark_tokens["text_primary"], "#E9E8E1")
self.assertIn("accent", dark_tokens)
self.assertEqual(light_tokens["background"], "#FCFCFB")
self.assertEqual(light_tokens["text_primary"], "#1A1A1A")
self.assertIn("accent", light_tokens)
self.assertNotEqual(dark_tokens["background"], light_tokens["background"])
```
**Mandate**: Any new semantic tokens must be additive to `LIGHT_TOKENS` and `DARK_TOKENS`, leaving all existing keys and values unchanged.

---

## 3. Centralized Semantic Design Token Formulation

### 3.1 Token Specification Matrix

| Token Key | Sand Light Value | Vast Dark Value | Semantic Purpose & Usage |
|:---|:---|:---|:---|
| `surface_pill` | `#1F1F1F` | `#1F1F1F` | Floating FlowBar pill surface (Obsidian across both modes for screen contrast) |
| `surface_pill_resting` | `rgba(0, 0, 0, 0.52)` | `rgba(0, 0, 0, 0.55)` | Resting 30×6px capsule translucent background |
| `surface_pill_card` | `#1F1F1F` | `#1F1F1F` | 416×80px notification card and 416×48px mini-toggle surface |
| `border_pill` | `#30302F` | `#30302F` | 1.5px hairline border surrounding floating pill elements |
| `border_pill_focus` | `#4D4A42` | `#5B5B59` | Active / focused border outline on pill controls |
| `text_pill_primary` | `#FCFCFB` | `#FCFCFB` | High-contrast stone typography on obsidian pill surfaces |
| `text_pill_muted` | `#71716E` | `#9D9C98` | Secondary metadata text ("in 2m", countdown, organizer) |
| `accent_emerald` | `#10B981` | `#10B981` | Wispr Flow status dot, voice typing active indicator, verified badge |
| `accent_emerald_tint`| `rgba(16, 185, 129, 0.12)` | `rgba(16, 185, 129, 0.16)` | Background tint for emerald status chips |
| `accent_coral` | `#FF5757` | `#FF5757` | Meeting recording live dot, stop button, danger/cancel action |
| `accent_coral_hover` | `#E04545` | `#E04545` | Hover fill on coral buttons |
| `accent_coral_tint` | `rgba(255, 87, 87, 0.12)` | `rgba(255, 87, 87, 0.16)` | Background tint for recording indicators and error states |
| `card_background` | `#FFFFFF` | `#202020` | Primary content card surface in Hub workspaces |
| `card_background_alt`| `#FAF9F7` | `#262625` | Alternate/secondary card surface in Hub workspaces |
| `split_button_fill` | `#E8DFC8` | `#383836` | Underlay auto-start countdown progress fill on FlowBar Join CTA |
| `tooltip_bg` | `#1A1A1A` | `#141414` | Tooltip container background |
| `tooltip_text` | `#FCFCFB` | `#E9E8E1` | Tooltip text color |
| `tooltip_border` | `#30302F` | `#383836` | Tooltip border hairline |
| `platform_teams` | `#6264A7` | `#6264A7` | Microsoft Teams branding glyph / badge |
| `platform_zoom` | `#2D8CFF` | `#2D8CFF` | Zoom branding glyph / badge |
| `platform_meet` | `#0F9D58` | `#0F9D58` | Google Meet branding glyph / badge |
| `platform_slack` | `#E01E5A` | `#E01E5A` | Slack Huddle branding glyph / badge |
| `platform_webex` | `#00A0D1` | `#00A0D1` | Webex branding glyph / badge |

### 3.2 Standardized Layout Spacing & Container Padding Constants

To enforce the 16px/12px/8px rhythm required by Wispr Flow standards, the following constants are added to `theme.py`:

```python
# Standardized Container Spacing & Layout Tokens (Milestone 8)
PADDING_CARD_PX: int = 16
PADDING_TOOLBAR_PX: int = 12
PADDING_BTN_GROUP_PX: int = 8

MARGINS_CARD: tuple[int, int, int, int] = (16, 16, 16, 16)
MARGINS_TOOLBAR: tuple[int, int, int, int] = (12, 8, 12, 8)
MARGINS_BTN_GROUP: tuple[int, int, int, int] = (8, 8, 8, 8)

SPACING_CARD_ITEMS_PX: int = 12
SPACING_TOOLBAR_ITEMS_PX: int = 8
SPACING_BUTTON_GROUP_ITEMS_PX: int = 6
```

Corresponding string tokens are included in `LIGHT_TOKENS` and `DARK_TOKENS`:
- `"padding_card": "16px"`
- `"padding_toolbar": "12px"`
- `"padding_btn_group": "8px"`

### 3.3 Strongly-Typed Token Access with Zero Breaking Changes

To enable modern attribute-based access (`t.surface_pill`) while maintaining full dictionary indexing (`t["surface_pill"]`):
```python
class ThemeTokenDict(dict):
    """Dictionary subclass supporting dot-notation attribute lookups."""
    def __getattr__(self, name: str) -> str:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"Theme token '{name}' not found.")

    def __setattr__(self, name: str, value: str) -> None:
        self[name] = value
```
`get_theme_tokens()` returns `ThemeTokenDict`, which satisfies `isinstance(t, dict)`, `t["key"]`, and `t.key`.

---

## 4. Codebase Audit: Hardcoded Hex Codes & Style Inconsistencies

### 4.1 Frequency Analysis of Hardcoded Hex Literals Outside `theme.py`
| File | Count | Dominant Patterns | Root Cause |
|:---|:---:|:---|:---|
| `components.py` | 99 | `#10B981`, `#EF4444`, `#3B82F6`, `#8B5CF6`, `#8B949E`, `#64748B` | Ad-hoc Tailwind utility palette hardcoded into widget constructors |
| `meeting_studio.py` | 46 | `#1E293B`, `#334155`, `#94A3B8`, `#F8FAFC`, `#0F172A`, `#3B82F6` | Tailwind Slate defaults used in `.get('key', '#XXXXXX')` fallbacks |
| `calendar_prompt.py` | 36 | `#334155`, `#1C1A18`, `#464EB8`, `#2D8CFF`, `#0F9D58` | Hardcoded platform badges and Slate fallbacks |
| `floating_pill.py` | 30 | `#1A1A1A`, `#30302F`, `#FCFCFB`, `#FF5757`, `#FFA946`, `#034F46` | Ad-hoc QColor and stylesheet literals inside paint events and sub-widgets |
| `animations.py` | 16 | `#10b981`, `#f43f5e`, `#f59e0b`, `#d6c5a2` | Lowercase hex codes inside `PulseBeacon` and `EqualizerBars` |
| `video_share_dialog.py` | 11 | `#0F172A`, `#080D15`, `#F8FAFC`, `#94A3B8`, `#0284C7`, `#0369A1` | Dark blue Slate theme hardcoded in modal QSS |
| `command_palette.py` | 10 | `#3B82F6`, `#161B22`, `#8B949E`, `#12161D`, `#F4F4F5` | VSCode/GitHub dark mode fallbacks |
| `meeting_prompt_popup.py` | 6 | `#464EB8`, `#2D8CFF`, `#0F9D58`, `#E01E5A`, `#00A0D1` | Platform badge colors |
| `notetaker_drawer.py` | 5 | `#736E62`, `#FF5757`, `#FFA946`, `#D2ACE9` | Hardcoded recording dot states |
| `onboarding_wizard.py` | 5 | `#000000`, `#30302F`, `#4DAA9D`, `#FCFCFB`, `#2A1F33` | Hardcoded pill preview styling |
| `floating_widget.py` | 3 | `#1A1A1A`, `#30302F`, `#FCFCFB` | Hardcoded speech bubble frame |
| `call_consent_dialog.py` | 1 | `#10B981` | Green scope text fallback |
| `tray_manager.py` | 1 | `#1A1A1A` | Menu text color fallback |

---

## 5. File-by-File Refactoring & Replacement Code Proposals

### 5.1 `src/voxlocal/ui/theme.py`

#### A. Add Semantic Tokens to `LIGHT_TOKENS` and `DARK_TOKENS`
```python
# --- Additions to LIGHT_TOKENS ---
"surface_pill": "#1F1F1F",
"surface_pill_resting": "rgba(0, 0, 0, 0.52)",
"surface_pill_card": "#1F1F1F",
"border_pill": "#30302F",
"border_pill_focus": "#4D4A42",
"text_pill_primary": "#FCFCFB",
"text_pill_muted": "#71716E",
"accent_emerald": "#10B981",
"accent_emerald_tint": "rgba(16, 185, 129, 0.12)",
"accent_coral": "#FF5757",
"accent_coral_hover": "#E04545",
"accent_coral_tint": "rgba(255, 87, 87, 0.12)",
"card_background": "#FFFFFF",
"card_background_alt": "#FAF9F7",
"split_button_fill": "#E8DFC8",
"padding_card": "16px",
"padding_toolbar": "12px",
"padding_btn_group": "8px",
"tooltip_bg": "#1A1A1A",
"tooltip_text": "#FCFCFB",
"tooltip_border": "#30302F",
"platform_teams": "#6264A7",
"platform_zoom": "#2D8CFF",
"platform_meet": "#0F9D58",
"platform_slack": "#E01E5A",
"platform_webex": "#00A0D1",

# --- Additions to DARK_TOKENS ---
"surface_pill": "#1F1F1F",
"surface_pill_resting": "rgba(0, 0, 0, 0.55)",
"surface_pill_card": "#1F1F1F",
"border_pill": "#30302F",
"border_pill_focus": "#5B5B59",
"text_pill_primary": "#FCFCFB",
"text_pill_muted": "#9D9C98",
"accent_emerald": "#10B981",
"accent_emerald_tint": "rgba(16, 185, 129, 0.16)",
"accent_coral": "#FF5757",
"accent_coral_hover": "#E04545",
"accent_coral_tint": "rgba(255, 87, 87, 0.16)",
"card_background": "#202020",
"card_background_alt": "#262625",
"split_button_fill": "#383836",
"padding_card": "16px",
"padding_toolbar": "12px",
"padding_btn_group": "8px",
"tooltip_bg": "#141414",
"tooltip_text": "#E9E8E1",
"tooltip_border": "#383836",
"platform_teams": "#6264A7",
"platform_zoom": "#2D8CFF",
"platform_meet": "#0F9D58",
"platform_slack": "#E01E5A",
"platform_webex": "#00A0D1",
```

#### B. Replace Hardcoded Values in `get_floating_pill_stylesheet()`
```python
# Before (lines 560-565):
QWidget#flowBarContainer {
    background-color: #000000;
    border: 1px solid #30302F;
    border-radius: 22px;
    color: #FCFCFB;
}

# Proposed Replacement:
QWidget#flowBarContainer {
    background-color: {t['surface_pill']};
    border: 1.5px solid {t['border_pill']};
    border-radius: 22px;
    color: {t['text_pill_primary']};
}
```

#### C. Replace Hardcoded Tooltips in `get_theme_stylesheet()`
```python
# Before (lines 528-535):
QToolTip {
    background-color: #1A1A1A;
    color: #FCFCFB;
    border: 1px solid #30302F;
    border-radius: 6px;
    padding: 5px 9px;
    font-size: 12px;
}

# Proposed Replacement:
QToolTip {
    background-color: {t['tooltip_bg']};
    color: {t['tooltip_text']};
    border: 1px solid {t['tooltip_border']};
    border-radius: 6px;
    padding: 5px 9px;
    font-size: 12px;
}
```

---

### 5.2 `src/voxlocal/ui/command_palette.py`

#### A. Remove Mismatched Tailwind / GitHub Colors
In `VoxCommandPaletteItem` (lines 48-81):
```python
# Before:
lbl_cat.setStyleSheet(
    f"font-size: 10px; font-weight: 800; color: {t.get('accent', '#3B82F6')}; "
    f"background: rgba(59, 130, 246, 0.12); border-radius: 4px; padding: 2px 6px;"
)
self.lbl_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {t.get('text_primary', '#F4F4F5')};")
self.lbl_desc.setStyleSheet(f"font-size: 11px; color: {t.get('text_muted', '#8B949E')};")

# Proposed Replacement:
is_dark = (self._theme or "").lower() == "dark"
cat_fg = t["accent_dawn"] if is_dark else t["accent_fathom"]
cat_bg = t["accent_dawn_tint"] if is_dark else t["accent_fathom_tint"]

lbl_cat.setStyleSheet(
    f"font-size: 10px; font-weight: 800; color: {cat_fg}; "
    f"background: {cat_bg}; border-radius: 4px; padding: 2px 6px;"
)
self.lbl_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {t['text_primary']};")
if command.description:
    self.lbl_desc.setStyleSheet(f"font-size: 11px; color: {t['text_muted']};")

# Shortcut badge:
lbl_sc.setStyleSheet(
    f"font-size: 11px; font-family: monospace; font-weight: 600; "
    f"color: {t['text_dim']}; "
    f"background: {t['surface_well']}; border: 1px solid {t['line']}; "
    f"border-radius: 4px; padding: 2px 6px;"
)
```

In `VoxCommandPaletteDialog.apply_theme()` (lines 177-216):
```python
# Before:
bg = t.get("surface", "#161B22")
border = t.get("line", "rgba(255, 255, 255, 0.12)")
text = t.get("text_primary", "#F4F4F5")
input_bg = t.get("input_bg", "#12161D")
...
border-color: {t.get('accent', '#3B82F6')};
background-color: rgba(59, 130, 246, 0.16);

# Proposed Replacement:
bg = t["surface"]
border = t["border"]
text = t["text_primary"]
input_bg = t["input_bg"]
focus_border = t["accent_dawn"] if self._theme == "dark" else t["accent_fathom"]
sel_bg = t["sidebar_active"]

self.container.setStyleSheet(
    f"QFrame#paletteContainer {{ background-color: {bg}; border: 1.5px solid {border}; border-radius: 14px; }}"
)
self.txt_search.setStyleSheet(
    f"QLineEdit#paletteSearchInput {{ background-color: {input_bg}; border: 1px solid {border}; "
    f"border-radius: 8px; color: {text}; font-size: 13.5px; padding: 8px 12px; }}"
    f"QLineEdit#paletteSearchInput:focus {{ border-color: {focus_border}; }}"
)
self.list_widget.setStyleSheet(
    f"QListWidget#paletteList {{ background: transparent; border: none; }} "
    f"QListWidget#paletteList::item {{ border-radius: 8px; margin-bottom: 2px; }} "
    f"QListWidget#paletteList::item:selected {{ background-color: {sel_bg}; color: {text}; }}"
)
```

#### B. Add Accessibility Attributes
In `VoxCommandPaletteDialog._build_ui()`:
```python
self.txt_search.setAccessibleName("Command search")
self.txt_search.setAccessibleDescription("Type a command or workspace name to search VOX")
self.list_widget.setAccessibleName("Matching commands list")
self.list_widget.setAccessibleDescription("Use Up and Down arrows to choose an action, Enter to execute")
```

---

### 5.3 `src/voxlocal/ui/settings_dialog.py`

#### A. Standardize Layout Padding to 16px / 12px / 8px
In `_make_card()` (line 350):
```python
# Before:
lay.setContentsMargins(18, 14, 18, 14)
lay.setSpacing(12)

# Proposed Replacement (Standardized 16px Card Padding):
from voxlocal.ui.theme import MARGINS_CARD, SPACING_CARD_ITEMS_PX
lay.setContentsMargins(*MARGINS_CARD)
lay.setSpacing(SPACING_CARD_ITEMS_PX)
```

In scroll container header/body (line 333):
```python
# Standardized page margin:
lay.setContentsMargins(28, 24, 28, 28)
lay.setSpacing(16)
```

#### B. Replace Raw Compute Labels with User-Friendly Names
In `_build_general_page()` (lines 498-510):
```python
# Before:
self.combo_model.addItem("large-v3 (Max Accuracy)", "large-v3")
self.combo_model.addItem("large-v3-turbo (Balanced)", "large-v3-turbo")
self.combo_model.addItem("medium (Fast)", "medium")

# Proposed Standard Friendly Options (Wispr Flow Parity):
self.combo_model.addItem("Maximum Accuracy (large-v3)", "large-v3")
self.combo_model.addItem("Balanced · Recommended (large-v3-turbo)", "large-v3-turbo")
self.combo_model.addItem("Fastest · Lower Memory (tiny / base / medium)", "medium")
```

#### C. Real-Time Audio Input Level Deflection Meter
Add a live microphone meter directly in `_build_general_page()`:
```python
from voxlocal.ui.components import VoxAudioVisualizerBar

# In _build_general_page():
self.mic_level_meter = VoxAudioVisualizerBar(bars_count=24, bar_width=4, bar_spacing=2)
self.mic_level_meter.setFixedHeight(22)
self.mic_level_meter.setToolTip("Live microphone input deflection level")
self.mic_level_meter.setAccessibleName("Microphone input level meter")
self._add_control_row(mic_lay, "Microphone Input Level", "Speak into your microphone to verify audio signal.", self.mic_level_meter)
```

#### D. Accessibility Attributes Across Settings
In `_add_toggle_row()` and `_add_control_row()`:
```python
sw.setAccessibleName(title)
sw.setAccessibleDescription(desc)
btn.setAccessibleName(f"Configure {title}")
btn.setAccessibleDescription(desc)
```

---

### 5.4 `src/voxlocal/ui/notetaker_page.py`

#### A. Standardize Card & Toolbar Padding
```python
# In _build_ui():
self.cal_card: cal_lay.setContentsMargins(*MARGINS_CARD)
self.lib_card: lib_lay.setContentsMargins(*MARGINS_CARD)
self.action_card: act_lay.setContentsMargins(*MARGINS_CARD)
```

#### B. Modern Illustrated Empty State Widget
When `meetings` is empty in `refresh_meetings_list()` (lines 440-460):
```python
# Before: meetings_list remains bare and empty

# Proposed Replacement:
if not meetings:
    if not hasattr(self, "empty_state_widget"):
        self.empty_state_widget = QWidget()
        elay = QVBoxLayout(self.empty_state_widget)
        elay.setContentsMargins(16, 24, 16, 24)
        elay.setSpacing(12)
        elay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_lbl = QLabel("🎙️")
        icon_lbl.setStyleSheet("font-size: 32px;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_lbl = QLabel("No meetings recorded yet")
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 700;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub_lbl = QLabel("Start a call in Google Meet, Zoom, or Teams, or launch an ad-hoc session.")
        sub_lbl.setObjectName("ntMuted")
        sub_lbl.setWordWrap(True)
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        btn_sample = QPushButton("Start Ad-hoc Capture")
        btn_sample.setObjectName("dawnBtn")
        btn_sample.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_sample.clicked.connect(self._on_toggle_recording_clicked)

        elay.addWidget(icon_lbl)
        elay.addWidget(title_lbl)
        elay.addWidget(sub_lbl)
        elay.addWidget(btn_sample, 0, Qt.AlignmentFlag.AlignCenter)

        self.lib_card.layout().addWidget(self.empty_state_widget)
    self.empty_state_widget.setVisible(True)
    self.meetings_list.setVisible(False)
else:
    if hasattr(self, "empty_state_widget"):
        self.empty_state_widget.setVisible(False)
    self.meetings_list.setVisible(True)
```

#### C. Accessibility Attributes
```python
self.meetings_list.setAccessibleName("Recorded meetings library")
self.meetings_list.setAccessibleDescription("List of previously recorded and transcribed meetings")
self.txt_search.setAccessibleName("Search meetings")
self.btn_start_adhoc.setAccessibleName("Start meeting capture")
```

---

### 5.5 `src/voxlocal/ui/main_window.py`

#### A. Comprehensive Screen-Reader Attributes on Sidebar Navigation
In `_build_ui()`:
```python
self.btn_global_search.setAccessibleName("Search VOX")
self.btn_global_search.setAccessibleDescription("Open global search palette across notes, dictations, and meetings (Ctrl+K)")

for btn, ws in zip(self.nav_buttons, self.WORKSPACES):
    btn.setAccessibleName(f"{ws['name']} workspace")
    btn.setAccessibleDescription(f"Navigate to {ws['name']} ({ws.get('shortcut', '')})")

self.theme_toggle.setAccessibleName("Theme toggle")
self.theme_toggle.setAccessibleDescription("Switch between Sand Light and Vast Dark color themes")
self.btn_toggle_float.setAccessibleName("Toggle FlowBar")
self.btn_toggle_float.setAccessibleDescription("Show or hide the floating dictation pill")
```

---

### 5.6 `src/voxlocal/ui/floating_pill.py`

#### A. Replace Hardcoded Hex Strings with Theme Tokens
In `VoxFlowBar._build_ui()` and `paintEvent()`:
```python
# Use centralized semantic tokens:
pill_bg = t["surface_pill"]
pill_border = t["border_pill"]
pill_text = t["text_pill_primary"]
pill_muted = t["text_pill_muted"]
pill_emerald = t["accent_emerald"]
pill_coral = t["accent_coral"]
pill_amber = t["accent_amber"]

# Bubble styling (line 286, 361):
self.setStyleSheet(
    f"QFrame {{ background-color: {t['surface_pill']}; border: 1.5px solid {t['border_pill']}; border-radius: 12px; }}"
)
self.lbl_text.setStyleSheet(f"color: {t['text_pill_primary']}; border: none; background: transparent;")

# RoundPillButton (lines 399-430):
# Replace hardcoded QColor instances with QColor(t['pill_button_bg']), QColor(t['accent_coral']), QColor(t['text_pill_primary'])
```

#### B. Floating Bar Accessibility
```python
self.setAccessibleName("VOX FlowBar")
self.setAccessibleDescription("Floating dictation and meeting control pill")
self.btn_notetaker_chip.setAccessibleName("Active meeting notetaker status")
self.btn_scratchpad.setAccessibleName("Open floating scratchpad")
```

---

### 5.7 `src/voxlocal/ui/components.py`

#### A. Modernize `VoxStatusPill` with Centralized Tokens
```python
# Before (lines 355-363): hardcoded hex dict with Tailwind values
# Proposed Replacement:
t = get_theme_tokens(self._theme)
colors = {
    "recording": (t["accent_coral"], t["accent_coral_tint"], "#FF8A8A", "#B91C1C"),
    "active": (t["accent_emerald"], t["accent_emerald_tint"], "#6EE7B7", "#047857"),
    "success": (t["accent_emerald"], t["accent_emerald_tint"], "#6EE7B7", "#047857"),
    "warning": (t["accent_amber"], "rgba(255, 169, 70, 0.14)", "#FCD34D", "#B45309"),
    "purple": (t["accent_purple"], "rgba(162, 110, 193, 0.14)", "#D2ACE9", "#6D28D9"),
    "sky": (t["accent_fathom"], t["accent_fathom_tint"], "#84CDC1", "#034F46"),
    "neutral": (t["text_dim"], t["surface_well"], t["text_secondary"], t["text_muted"]),
}
```

#### B. Enforce Default 16px Padding on `VoxCard`
```python
class VoxCard(QFrame):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("voxCard")
        t = get_theme_tokens("light")
        self.setStyleSheet(
            f"QFrame#voxCard {{ background-color: {t['card_background']}; "
            f"border: 1px solid {t['border']}; border-radius: 12px; }}"
        )
```

---

## 6. Verification & Automated Test Strategy

To ensure zero regressions and verify compliance, Milestone 8 will introduce `tests/test_ux_accessibility_regressions.py` covering:

1. **Token Completeness Test**:
   - Asserts all 22 required semantic tokens exist in both `LIGHT_TOKENS` and `DARK_TOKENS`.
   - Asserts all existing legacy keys (`bg`, `surface`, `text`, `accent`, etc.) retain their exact values.
2. **Padding & Layout Constants Test**:
   - Asserts `PADDING_CARD_PX == 16`, `PADDING_TOOLBAR_PX == 12`, `PADDING_BTN_GROUP_PX == 8`.
   - Asserts `MARGINS_CARD == (16, 16, 16, 16)`, `MARGINS_TOOLBAR == (12, 8, 12, 8)`, `MARGINS_BTN_GROUP == (8, 8, 8, 8)`.
3. **Accessibility Compliance Test**:
   - Instantiates `MainWindow`, `VoxCommandPaletteDialog`, `SettingsWorkspaceWidget`, and `NotetakerPage`.
   - Walks widget trees and asserts `accessibleName()` is non-empty for all interactive `QPushButton`, `QLineEdit`, `QComboBox`, and `QListWidget` instances.
4. **Style Sheet Hex Exclusion Test**:
   - Inspects generated QSS from `get_theme_stylesheet()`, `get_dialog_stylesheet()`, and `get_floating_pill_stylesheet()`.
   - Asserts zero occurrences of deprecated Tailwind Slate hexes (`#1E293B`, `#334155`, `#94A3B8`, `#3B82F6`).

---

## 7. Next Steps for Implementation Team
1. Apply the additive token updates and layout constants to `src/voxlocal/ui/theme.py`.
2. Apply file-by-file refactoring across `command_palette.py`, `settings_dialog.py`, `notetaker_page.py`, `main_window.py`, and `components.py`.
3. Run existing test suites (`test_theme_and_animations.py`, `test_e2e_product_ux_and_architecture.py`, `test_wispr_parity_features.py`) via `.venv\Scripts\pytest.exe` to guarantee zero regressions.
4. Add `tests/test_ux_accessibility_regressions.py` to validate token presence and accessibility compliance.

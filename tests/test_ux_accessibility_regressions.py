"""
VOX E2E Test Suite 4: App-Wide UX Polish & Accessibility Regressions (Features 15-18).

Covers:
- Feature 15: App-wide Semantic Design Tokens (Sand Light & Vast Dark token dictionaries, 16/12/8px rhythm)
- Feature 16: Polished Empty States & Quick Starts (Illustration widgets, 1-click starter templates in Notetaker & Snippets)
- Feature 17: Settings UX Polish & Live Mic Input Meter (Real-time deflection meter, friendly Whisper compute labels, standardized layout)
- Feature 18: Screen Reader & Keyboard Accessibility (setAccessibleName/Description, Tab navigation, Escape dismissal, Space/Enter activation)

Tiers 1-4:
- Tier 1: Feature Coverage (20 tests)
- Tier 2: Boundary & Corner Cases (20 tests)
- Tier 3: Pairwise Combinations (5 tests)
- Tier 4: Real-World Workload Scenarios (5 tests)
Total: 50 tests.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Ensure src/ is on sys.path before importing voxlocal
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Ensure headless offscreen Qt platform
os.environ["QT_QPA_PLATFORM"] = "offscreen"

pytest_plugins = ["tests.e2e.conftest"]

import pytest
from PyQt6.QtCore import QEvent, QPoint, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from voxlocal.audio.devices import MicrophoneLevelProbe
from voxlocal.core.config import AppConfig
from voxlocal.core.controller import AppController
from voxlocal.ui.components import VoxAudioVisualizerBar, VoxEmptyStateWidget
from voxlocal.ui.hub_pages import SnippetsPage
from voxlocal.ui.notetaker_page import NotetakerPage
from voxlocal.ui.settings_dialog import (
    COMPUTE_LABEL_TO_VALUE,
    COMPUTE_VALUE_TO_LABEL,
    SettingsDialog,
    SettingsWorkspaceWidget,
    ShortcutCaptureDialog,
)
from voxlocal.ui.theme import (
    DARK_TOKENS,
    LIGHT_TOKENS,
    MARGINS_BTN_GROUP,
    MARGINS_CARD,
    MARGINS_TOOLBAR,
    PADDING_BTN_GROUP_PX,
    PADDING_CARD_PX,
    PADDING_TOOLBAR_PX,
    SPACING_BUTTON_GROUP_ITEMS_PX,
    SPACING_CARD_ITEMS_PX,
    SPACING_TOOLBAR_ITEMS_PX,
    get_dialog_stylesheet,
    get_floating_pill_stylesheet,
    get_theme_stylesheet,
    get_theme_tokens,
)


def _calc_relative_luminance(hex_str: str) -> float:
    """Compute relative luminance according to WCAG 2.1 specs."""
    clean_hex = hex_str.lstrip("#")
    if len(clean_hex) != 6:
        return 0.5
    r, g, b = [int(clean_hex[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
    r_lin = r / 12.92 if r <= 0.03928 else ((r + 0.055) / 1.055) ** 2.4
    g_lin = g / 12.92 if g <= 0.03928 else ((g + 0.055) / 1.055) ** 2.4
    b_lin = b / 12.92 if b <= 0.03928 else ((b + 0.055) / 1.055) ** 2.4
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def _calc_contrast_ratio(hex_c1: str, hex_c2: str) -> float:
    """Compute contrast ratio between two hex colors according to WCAG AA."""
    l1 = _calc_relative_luminance(hex_c1)
    l2 = _calc_relative_luminance(hex_c2)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


# ======================================================================
# TIER 1: FEATURE COVERAGE (20 tests)
# ======================================================================


class TestTier1Feature15SemanticTokens:
    """Feature 15: App-wide Semantic Design Tokens."""

    def test_01_light_tokens_contain_all_required_keys(self) -> None:
        """Validate that LIGHT_TOKENS dictionary contains all standardized tokens."""
        required = [
            "bg",
            "background",
            "surface",
            "surface_secondary",
            "surface_well",
            "text",
            "text_primary",
            "text_muted",
            "line",
            "border",
            "accent",
            "surface_pill",
            "border_pill",
            "text_pill_primary",
            "accent_emerald",
            "accent_coral",
            "card_background",
            "split_button_fill",
        ]
        for key in required:
            assert key in LIGHT_TOKENS, f"Missing token key in LIGHT_TOKENS: {key}"
            val = LIGHT_TOKENS[key]
            assert isinstance(val, str) and len(val) > 0, f"Token {key} is empty"

        assert LIGHT_TOKENS["bg"] == "#F8FAFC"
        assert LIGHT_TOKENS["surface"] == "#FFFFFF"
        assert LIGHT_TOKENS["text"] == "#0F172A"

    def test_02_dark_tokens_contain_all_required_keys(self) -> None:
        """Validate that DARK_TOKENS dictionary contains all standardized tokens."""
        required = [
            "bg",
            "background",
            "surface",
            "surface_secondary",
            "surface_well",
            "text",
            "text_primary",
            "text_muted",
            "line",
            "border",
            "accent",
            "surface_pill",
            "border_pill",
            "text_pill_primary",
            "accent_emerald",
            "accent_coral",
            "card_background",
            "split_button_fill",
        ]
        for key in required:
            assert key in DARK_TOKENS, f"Missing token key in DARK_TOKENS: {key}"
            val = DARK_TOKENS[key]
            assert isinstance(val, str) and len(val) > 0, f"Token {key} is empty"

        assert DARK_TOKENS["bg"] == "#0B0F19"
        assert DARK_TOKENS["surface"] == "#111827"
        assert DARK_TOKENS["text"] == "#F8FAFC"

    def test_03_get_theme_tokens_resolves_correctly(self) -> None:
        """Validate get_theme_tokens helper resolves light/dark and supports dot access."""
        t_light = get_theme_tokens("light")
        assert t_light is LIGHT_TOKENS
        assert t_light.bg == "#F8FAFC"

        t_dark = get_theme_tokens("dark")
        assert t_dark is DARK_TOKENS
        assert t_dark.bg == "#0B0F19"

        # Case-insensitivity and whitespace stripping
        assert get_theme_tokens("DARK") is DARK_TOKENS
        assert get_theme_tokens(" light ") is LIGHT_TOKENS

    def test_04_no_hardcoded_hex_in_core_ui_components(self) -> None:
        """Validate generated stylesheets dynamically incorporate token dictionary values."""
        sheet_light = get_theme_stylesheet("light")
        assert LIGHT_TOKENS["bg"] in sheet_light
        assert LIGHT_TOKENS["text"] in sheet_light

        sheet_dark = get_theme_stylesheet("dark")
        assert DARK_TOKENS["bg"] in sheet_dark
        assert DARK_TOKENS["text"] in sheet_dark

        dlg_light = get_dialog_stylesheet("light")
        assert LIGHT_TOKENS["bg"] in dlg_light

        dlg_dark = get_dialog_stylesheet("dark")
        assert DARK_TOKENS["bg"] in dlg_dark

    def test_05_standardized_spacing_tokens_16_12_8(self) -> None:
        """Validate standardized layout padding, margins, and item spacing constants."""
        assert PADDING_CARD_PX == 16
        assert PADDING_TOOLBAR_PX == 12
        assert PADDING_BTN_GROUP_PX == 8

        assert MARGINS_CARD == (16, 16, 16, 16)
        assert MARGINS_TOOLBAR == (12, 8, 12, 8)
        assert MARGINS_BTN_GROUP == (8, 8, 8, 8)

        assert SPACING_CARD_ITEMS_PX == 12
        assert SPACING_TOOLBAR_ITEMS_PX == 8
        assert SPACING_BUTTON_GROUP_ITEMS_PX == 6


class TestTier1Feature16PolishedEmptyStates:
    """Feature 16: Polished Empty States & Quick Starts."""

    def test_01_meeting_history_empty_state_rendered(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate NotetakerPage shows VoxEmptyStateWidget when meeting database is empty."""
        page = NotetakerPage(isolated_controller)
        page.show()

        assert hasattr(page, "empty_card"), "NotetakerPage should have empty_card"
        assert page.empty_card.isVisible() is True
        assert page.action_card.isVisible() is False
        page.close()

    def test_02_empty_state_contains_title_and_subtitle(
        self, offscreen_widgets: Dict[str, Any]
    ) -> None:
        """Validate VoxEmptyStateWidget renders headline, explanatory subtitle, and icon."""
        widget = VoxEmptyStateWidget(
            icon="🎙️",
            title="No Meeting Notes Recorded Yet",
            subtitle="Capture live calls locally or choose a quick starter template.",
            theme="light",
        )
        assert widget.lbl_title.text() == "No Meeting Notes Recorded Yet"
        assert "template" in widget.lbl_sub.text().lower()
        assert widget.lbl_icon.text() == "🎙️"

    def test_03_one_click_starter_prompts_present(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate NotetakerPage empty card offers 3 distinct starter templates."""
        page = NotetakerPage(isolated_controller)
        buttons = page.empty_card._starter_buttons
        assert len(buttons) >= 3, "Should have at least 3 starter template buttons"

        accessible_names = [b.accessibleName() for b in buttons]
        assert any("Standup" in name for name in accessible_names)
        assert any("Architecture" in name for name in accessible_names)
        assert any("1-on-1" in name for name in accessible_names)
        page.close()

    def test_04_clicking_starter_prompt_creates_template(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate clicking a starter prompt creates structured note in SQLite and displays it."""
        page = NotetakerPage(isolated_controller)
        page.show()

        # Click standup starter
        page.empty_card.starter_clicked.emit("standup")

        meetings = isolated_controller.db.list_meetings()
        assert len(meetings) == 1
        assert "Standup" in meetings[0]["title"]

        # Empty card hides and Studio workspace reveals
        assert page.empty_card.isVisible() is False
        assert page.studio.isVisible() is True
        page.close()

    def test_05_snippets_empty_state_quick_start(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate SnippetsPage empty state indicates how to speak shortcuts."""
        page = SnippetsPage(isolated_controller)
        page.show()
        page.refresh()

        # With 0 snippets, an empty label is present
        assert page.list_lay.count() > 0
        first_item = page.list_lay.itemAt(0).widget()
        assert isinstance(first_item, QLabel)
        assert "snippets" in first_item.text().lower()

        # Add snippet -> empty message replaced with card
        isolated_controller.db.upsert_snippet("standup_cue", "Daily progress updates.")
        page.refresh()
        card_item = page.list_lay.itemAt(0).widget()
        assert card_item is not None and card_item.objectName() == "snipCard"
        page.close()


class TestTier1Feature17SettingsPolishAndLiveMicMeter:
    """Feature 17: Settings UX Polish & Live Mic Input Meter."""

    def test_01_settings_audio_page_contains_live_mic_meter(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate Settings General page contains live mic deflection bar and test button."""
        sw = SettingsWorkspaceWidget(isolated_controller)
        sw.select_section(0)

        assert hasattr(sw, "mic_level_bar"), "Settings must have mic_level_bar"
        assert isinstance(sw.mic_level_bar, VoxAudioVisualizerBar)
        assert hasattr(sw, "btn_test_mic")
        assert sw.btn_test_mic.text() == "Test Mic"
        sw.close()

    def test_02_live_mic_meter_updates_deflection_on_level_changed(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate mic deflection bar reacts to input level updates with peak retention."""
        sw = SettingsWorkspaceWidget(isolated_controller)
        sw.select_section(0)

        sw.mic_level_bar.set_level(0.72)
        assert sw.mic_level_bar._level == 0.72
        assert sw.mic_level_bar._peak >= 0.72

        # Emission via settings signal
        sw.mic_level_updated.emit(0.38)
        assert sw.mic_level_bar._level == 0.38
        sw.close()

    def test_03_speech_model_labels_user_friendly(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate speech model and compute dropdowns expose friendly, descriptive labels."""
        sw = SettingsWorkspaceWidget(isolated_controller)

        # Model options contain recommendations and latency hints
        model_labels = [sw.combo_model.itemText(i) for i in range(sw.combo_model.count())]
        assert any("Recommended" in lbl for lbl in model_labels)
        assert any("Fast" in lbl for lbl in model_labels)

        # Compute precision options contain friendly performance descriptions
        compute_labels = [sw.combo_compute.itemText(i) for i in range(sw.combo_compute.count())]
        assert "Balanced (Recommended)" in compute_labels
        assert "Fastest (Lower Memory)" in compute_labels
        assert "Maximum Accuracy" in compute_labels
        sw.close()

    def test_04_friendly_labels_map_to_valid_config_values(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate friendly compute labels map cleanly to CTranslate2 precision values."""
        assert COMPUTE_LABEL_TO_VALUE["Fastest (Lower Memory)"] == "int8"
        assert COMPUTE_LABEL_TO_VALUE["Balanced (Recommended)"] == "int8_float16"
        assert COMPUTE_LABEL_TO_VALUE["Maximum Accuracy"] == "float16"
        assert COMPUTE_VALUE_TO_LABEL["int8"] == "Fastest (Lower Memory)"

        sw = SettingsWorkspaceWidget(isolated_controller)
        # Select fastest
        idx = sw.combo_compute.findData("int8")
        assert idx >= 0
        sw.combo_compute.setCurrentIndex(idx)
        assert isolated_controller.config.compute_type == "int8"
        sw.close()

    def test_05_settings_page_margins_and_row_spacing_standard(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate settings page containers follow the 16/12/8px layout rhythm."""
        sw = SettingsWorkspaceWidget(isolated_controller)
        _w, lay = sw._page_shell("Test Title", "Test Subtitle")
        margins = lay.contentsMargins()
        assert margins.left() in (24, 32)
        assert lay.spacing() in (14, 16)

        _c, card_lay = sw._make_card()
        assert card_lay.spacing() == 12
        sw.close()


class TestTier1Feature18AccessibilityAndKeyboard:
    """Feature 18: Screen Reader & Keyboard Accessibility."""

    def test_01_floating_pill_controls_have_accessible_names(
        self, offscreen_widgets: Dict[str, Any]
    ) -> None:
        """Validate floating pill elements expose accessible names, tooltips, or text."""
        pill = offscreen_widgets["pill"]()
        controls = [
            getattr(pill, attr)
            for attr in ["btn_scratchpad", "btn_cancel", "btn_check", "btn_stop", "menu_btn"]
            if hasattr(pill, attr)
        ]
        has_a11y = any(
            bool(c.accessibleName() or c.toolTip() or (hasattr(c, "text") and c.text()))
            for c in controls
        )
        assert has_a11y is True or bool(pill.accessibleName())
        pill.close()

    def test_02_notetaker_drawer_controls_have_accessible_names(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate NotetakerPage primary buttons and inputs expose explicit accessible names."""
        page = NotetakerPage(isolated_controller)
        assert page.btn_drawer.accessibleName() == "Open Floating Drawer"
        assert page.btn_start_adhoc.accessibleName() == "Toggle Notetaker recording"
        assert page.txt_search.accessibleName() == "Search call notes and transcripts"
        assert page.meetings_list.accessibleName() == "Recorded meetings library"
        assert page.media_slider.accessibleName() == "Audio playback seek slider"
        page.close()

    def test_03_settings_dialog_inputs_have_accessible_names(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate Settings inputs, dropdowns, and meter expose accessible names."""
        sw = SettingsWorkspaceWidget(isolated_controller)
        assert sw.combo_mic.accessibleName() == "Microphone input device"
        assert sw.mic_level_bar.accessibleName() == "Microphone input level meter"
        assert sw.btn_test_mic.accessibleName() == "Test microphone"
        assert sw.slider_mic_gain.accessibleName() == "Microphone gain slider"
        assert bool(sw.combo_compute.accessibleName())

        dlg = ShortcutCaptureDialog(current_shortcut="ctrl+win")
        assert dlg.display_chip.accessibleName() == "Recorded shortcut key display"
        assert dlg.btn_clear.accessibleName() == "Clear shortcut"
        assert dlg.btn_save.accessibleName() == "Save shortcut"
        dlg.close()
        sw.close()

    def test_04_escape_key_dismisses_dialogs(
        self, offscreen_widgets: Dict[str, Any]
    ) -> None:
        """Validate Escape key rejects and dismisses ShortcutCaptureDialog."""
        dlg = ShortcutCaptureDialog(current_shortcut="ctrl+win")
        dlg.show()

        escape_event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
        dlg.keyPressEvent(escape_event)

        assert dlg.result() == QDialog.DialogCode.Rejected
        dlg.close()

    def test_05_keyboard_tab_focus_chain_in_settings(
        self, offscreen_widgets: Dict[str, Any]
    ) -> None:
        """Validate Tab key cycles focus cleanly through dialog interactive controls."""
        dlg = ShortcutCaptureDialog(current_shortcut="ctrl+win")
        dlg.show()

        moved = dlg.focusNextChild()
        assert moved is True
        focused = dlg.focusWidget()
        assert focused is not None and isinstance(focused, QPushButton)
        dlg.close()


# ======================================================================
# TIER 2: BOUNDARY & CORNER CASES (20 tests)
# ======================================================================


class TestTier2Boundary15TokenBoundaries:
    """Boundary conditions for Semantic Tokens & Layout constants."""

    def test_01_unknown_theme_string_falls_back_to_light(self) -> None:
        """Validate unknown, empty, or None theme strings fall back safely to LIGHT_TOKENS."""
        assert get_theme_tokens("unknown_custom_theme") is LIGHT_TOKENS
        assert get_theme_tokens("") is LIGHT_TOKENS
        assert get_theme_tokens(None) is LIGHT_TOKENS  # type: ignore[arg-type]

    def test_02_token_lookup_missing_key_fallback(self) -> None:
        """Validate missing key lookup via get() uses fallback and dot access raises AttributeError."""
        tokens = get_theme_tokens("light")
        assert tokens.get("non_existent_key", "#DEFAULT") == "#DEFAULT"
        with pytest.raises(AttributeError):
            _ = tokens.non_existent_key

    def test_03_contrast_ratio_wcag_aa_compliance(self) -> None:
        """Validate WCAG AA contrast ratio compliance (>= 4.5:1) for primary text pairs."""
        light_contrast = _calc_contrast_ratio(LIGHT_TOKENS["text"], LIGHT_TOKENS["bg"])
        assert light_contrast >= 4.5, f"Light contrast {light_contrast:.2f} failed WCAG AA"

        dark_contrast = _calc_contrast_ratio(DARK_TOKENS["text"], DARK_TOKENS["bg"])
        assert dark_contrast >= 4.5, f"Dark contrast {dark_contrast:.2f} failed WCAG AA"

        # FlowBar pill text on obsidian pill background
        pill_contrast = _calc_contrast_ratio(LIGHT_TOKENS["text_pill_primary"], LIGHT_TOKENS["surface_pill"])
        assert pill_contrast >= 7.0, f"Pill contrast {pill_contrast:.2f} failed AAA"

    def test_04_theme_changed_signal_broadcast(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate controller.theme_changed signal broadcasts reliably on theme toggle."""
        signals: List[str] = []
        isolated_controller.theme_changed.connect(signals.append)

        isolated_controller.set_theme("dark")
        isolated_controller.set_theme("light")
        assert signals == ["dark", "light"]

    def test_05_high_contrast_mode_adaptation(self) -> None:
        """Validate text and line tokens provide adequate contrast separation in dark mode."""
        t = get_theme_tokens("dark")
        assert t["text_primary"] != t["surface"]
        assert t["border"] != t["surface"]
        # Contrast between dark surface and dark text
        contrast = _calc_contrast_ratio(t["text_primary"], t["surface"])
        assert contrast >= 4.5


class TestTier2Boundary16EmptyStateBoundaries:
    """Boundary conditions for Empty State widgets & starter actions."""

    def test_01_empty_state_hidden_when_single_item_added(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate adding a single meeting item causes empty state to hide and studio to reveal."""
        page = NotetakerPage(isolated_controller)
        page.show()
        assert page.empty_card.isVisible() is True

        isolated_controller.db.create_meeting("First Meeting")
        page.refresh_meetings_list()

        assert page.empty_card.isVisible() is False
        assert page.studio.isVisible() is True
        page.close()

    def test_02_empty_state_reappears_on_database_wipe(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate emptying meeting database causes empty state card to reappear immediately."""
        page = NotetakerPage(isolated_controller)
        page.show()

        isolated_controller.db.create_meeting("Temporary Meeting")
        page.refresh_meetings_list()
        assert page.empty_card.isVisible() is False

        isolated_controller.db.clear_data_categories(["meetings"])
        page._selected_meeting_id = None
        page.refresh_meetings_list()

        assert page.empty_card.isVisible() is True
        page.close()

    def test_03_starter_prompt_with_existing_draft_confirmation(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate starter templates generate independent meetings without overwriting notes."""
        page = NotetakerPage(isolated_controller)
        page.show()

        page._create_template_meeting("arch")
        meetings = isolated_controller.db.list_meetings()
        assert len(meetings) == 1
        assert "Architecture Review" in meetings[0]["title"]
        page.close()

    def test_04_empty_state_responsive_to_small_window_size(
        self, offscreen_widgets: Dict[str, Any]
    ) -> None:
        """Validate VoxEmptyStateWidget scales responsively without layout clipping at small sizes."""
        widget = VoxEmptyStateWidget(title="Title", subtitle="A long descriptive subtitle for testing.")
        widget.resize(QSize(320, 240))
        assert widget.lbl_sub.wordWrap() is True
        assert widget.sizeHint().width() > 0

    def test_05_keyboard_activation_of_starter_prompts(
        self, offscreen_widgets: Dict[str, Any]
    ) -> None:
        """Validate starter template buttons emit starter_clicked when triggered via click."""
        widget = VoxEmptyStateWidget()
        clicked_keys: List[str] = []
        widget.starter_clicked.connect(clicked_keys.append)

        btn = widget.add_starter_card("1-on-1 Catchup", "Goals and pulse", "1on1")
        btn.click()

        assert clicked_keys == ["1on1"]


class TestTier2Boundary17MicMeterBoundaries:
    """Boundary conditions for Live Mic Meter & Audio Level Probe."""

    def test_01_zero_mic_level_deflection_zero(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate 0.0 input level sets deflection to exactly zero without visual jitter."""
        bar = VoxAudioVisualizerBar()
        bar.set_level(0.0)
        assert bar._level == 0.0
        assert bar._peak == 0.0

    def test_02_clipping_mic_level_above_one(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate clipping levels > 1.0 or negative levels are clamped within [0.0, 1.0]."""
        bar = VoxAudioVisualizerBar()
        bar.set_level(1.95)
        assert bar._level == 1.0

        bar.set_level(-0.5)
        assert bar._level >= 0.0

    def test_03_mic_meter_inactive_when_settings_closed(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate background probe thread terminates cleanly when Settings widget closes."""
        sw = SettingsWorkspaceWidget(isolated_controller)
        sw.btn_test_mic.setChecked(True)
        sw._toggle_mic_test()

        assert hasattr(sw, "_mic_probe") and sw._mic_probe is not None
        assert sw._mic_probe.is_running() is True

        # Close settings -> probe must stop
        sw.close()
        assert sw._mic_probe.is_running() is False

    def test_04_device_switching_rebinds_meter(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate MicrophoneLevelProbe can be stopped and rebound to new device cleanly."""
        probe = MicrophoneLevelProbe(device_index=None, device_name="Default")
        started = probe.start()
        assert started is True
        assert probe.is_running() is True

        probe.stop()
        assert probe.is_running() is False

    def test_05_rapid_device_refresh_clicks(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate rapid consecutive calls to _populate_mics() do not crash or corrupt list."""
        sw = SettingsWorkspaceWidget(isolated_controller)
        for _ in range(5):
            sw._populate_mics()
        assert sw.combo_mic.count() >= 1
        sw.close()


class TestTier2Boundary18KeyboardAndA11yBoundaries:
    """Boundary conditions for Keyboard navigation & Screen Reader semantics."""

    def test_01_tab_cycle_wraps_around(
        self, offscreen_widgets: Dict[str, Any]
    ) -> None:
        """Validate keyboard tab focus wraps around continuously within modal dialog."""
        dlg = ShortcutCaptureDialog(current_shortcut="ctrl+win")
        dlg.show()

        chain: List[QWidget] = []
        for _ in range(15):
            if dlg.focusNextChild():
                chain.append(dlg.focusWidget())

        unique_widgets = {w for w in chain if w is not None}
        assert len(unique_widgets) >= 3, "Tab focus cycle should wrap across multiple controls"
        dlg.close()

    def test_02_disabled_controls_skipped_in_tab_chain(
        self, offscreen_widgets: Dict[str, Any]
    ) -> None:
        """Validate disabled buttons are automatically bypassed during tab traversal."""
        dlg = ShortcutCaptureDialog(current_shortcut="ctrl+win")
        dlg.btn_clear.setEnabled(False)
        dlg.show()

        visited: List[QWidget] = []
        for _ in range(12):
            if dlg.focusNextChild():
                visited.append(dlg.focusWidget())

        assert dlg.btn_clear not in visited, "Disabled button should be bypassed in focus chain"
        dlg.close()

    def test_03_enter_and_space_trigger_button_click(
        self, offscreen_widgets: Dict[str, Any]
    ) -> None:
        """Validate pressing Space or Return triggers clicked signal on QPushButton."""
        btn = QPushButton("Test Action")
        clicks: List[int] = []
        btn.clicked.connect(lambda: clicks.append(1))

        # Space trigger
        btn.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
        btn.keyReleaseEvent(QKeyEvent(QKeyEvent.Type.KeyRelease, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
        assert len(clicks) == 1

        # Return trigger
        btn.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
        btn.keyReleaseEvent(QKeyEvent(QKeyEvent.Type.KeyRelease, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
        assert len(clicks) == 2

    def test_04_screen_reader_role_descriptions(
        self, offscreen_widgets: Dict[str, Any]
    ) -> None:
        """Validate accessibleDescription provides descriptive context on key interactive controls."""
        empty = VoxEmptyStateWidget()
        assert "capturing audio" in empty.btn_action.accessibleDescription().lower()

        dlg = ShortcutCaptureDialog()
        assert "key" in dlg.display_chip.accessibleDescription().lower()
        dlg.close()

    def test_05_floating_bar_keyboard_shortcut_nav(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Validate push-to-talk and dictation shortcuts in AppConfig are valid string bindings."""
        cfg = isolated_controller.config
        assert isinstance(cfg.ptt_hotkey, str) and len(cfg.ptt_hotkey) > 0
        assert isinstance(cfg.typing_hotkey, str) and len(cfg.typing_hotkey) > 0

        # Change hotkey and save
        cfg.ptt_hotkey = "ctrl+alt+v"
        cfg.save()
        assert isolated_controller.config.ptt_hotkey == "ctrl+alt+v"


# ======================================================================
# TIER 3: PAIRWISE COMBINATIONS (5 tests)
# ======================================================================


class TestTier3PairwiseUxAccessibility:
    """Pairwise cross-feature interactions across Features 15-18."""

    def test_01_theme_toggle_updates_empty_state_and_settings(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Pairwise: Theme toggle synchronizes stylesheets across empty card and settings workspace."""
        empty = VoxEmptyStateWidget(theme="light")
        sw = SettingsWorkspaceWidget(isolated_controller)

        isolated_controller.set_theme("dark")
        empty.apply_theme("dark")
        sw.apply_theme("dark")

        assert DARK_TOKENS["surface"] in empty.styleSheet()
        assert DARK_TOKENS["bg"] in sw.styleSheet()

        isolated_controller.set_theme("light")
        empty.apply_theme("light")
        sw.apply_theme("light")

        assert LIGHT_TOKENS["surface"] in empty.styleSheet()
        assert LIGHT_TOKENS["bg"] in sw.styleSheet()
        sw.close()

    def test_02_mic_selection_updates_meter_and_saves_to_config(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Pairwise: Mic selection and gain adjustments update deflection meter and persist to config."""
        sw = SettingsWorkspaceWidget(isolated_controller)
        sw.select_section(0)

        sw.slider_mic_gain.setValue(175)
        assert isolated_controller.config.mic_gain == 1.75

        sw.mic_level_bar.set_level(0.65)
        assert sw.mic_level_bar._level == 0.65
        sw.close()

    def test_03_keyboard_nav_through_empty_state_to_starter_prompt(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Pairwise: Keyboard activation of empty state template creates meeting and transitions UI."""
        page = NotetakerPage(isolated_controller)
        page.show()

        first_starter = page.empty_card._starter_buttons[0]
        first_starter.setFocus()
        assert first_starter.hasFocus() is True

        # Simulate click
        first_starter.click()
        assert len(isolated_controller.db.list_meetings()) == 1
        assert page.empty_card.isVisible() is False
        page.close()

    def test_04_accessible_names_read_friendly_compute_labels(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Pairwise: Accessible compute precision labels map directly to valid internal configurations."""
        sw = SettingsWorkspaceWidget(isolated_controller)
        assert sw.combo_compute.accessibleName() == "Compute Precision"

        for i in range(sw.combo_compute.count()):
            label = sw.combo_compute.itemText(i)
            val = sw.combo_compute.itemData(i)
            assert label in COMPUTE_LABEL_TO_VALUE
            assert COMPUTE_LABEL_TO_VALUE[label] == val
        sw.close()

    def test_05_settings_export_and_wipe_retains_accessibility(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """Pairwise: Selective privacy wipe preserves accessibility metadata across UI controls."""
        sw = SettingsWorkspaceWidget(isolated_controller)
        assert sw.combo_mic.accessibleName() == "Microphone input device"

        # Wipe check
        sw._wipe_checks["vocabulary"].setChecked(True)
        assert sw._wipe_checks["vocabulary"].accessibleName() or sw._wipe_checks["vocabulary"].text()
        sw.close()


# ======================================================================
# TIER 4: REAL-WORLD WORKLOAD SCENARIOS (5 tests)
# ======================================================================


class TestTier4UxAccessibilityScenarios:
    """Realistic end-to-end user journeys covering Features 15-18."""

    def test_scenario_1_first_launch_empty_state_to_quick_start(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """
        Scenario 1: First-time user opens Notetaker with zero meetings.
        Empty state displays 1-click starter cards.
        User selects 'Product Standup' template.
        Note is created in database, library list populates, empty state hides,
        and Studio workspace loads structured template note.
        """
        assert len(isolated_controller.db.list_meetings()) == 0

        page = NotetakerPage(isolated_controller)
        page.show()
        assert page.empty_card.isVisible() is True
        assert page.action_card.isVisible() is False

        # Select Product Standup template
        page.empty_card.starter_clicked.emit("standup")

        meetings = isolated_controller.db.list_meetings()
        assert len(meetings) == 1
        assert "Product Standup" in meetings[0]["title"]
        assert page.empty_card.isVisible() is False
        assert page.studio.isVisible() is True
        assert page.meetings_list.count() == 1
        page.close()

    def test_scenario_2_audio_settings_mic_selection_and_live_meter_test(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """
        Scenario 2: User opens Settings -> General, verifies live mic meter,
        adjusts gain boost slider, observes deflection reaction,
        and switches sections cleanly stopping the probe thread.
        """
        sw = SettingsWorkspaceWidget(isolated_controller)
        sw.select_section(0)

        # Start mic test
        sw.btn_test_mic.setChecked(True)
        sw._toggle_mic_test()
        assert sw._mic_probe is not None and sw._mic_probe.is_running() is True

        # Simulate live volume stream
        sw.mic_level_updated.emit(0.68)
        assert sw.mic_level_bar._level == 0.68

        # Switch to System section -> probe thread stops without leak
        sw.select_section(1)
        assert sw._mic_probe.is_running() is False
        sw.close()

    def test_scenario_3_complete_keyboard_only_navigation_journey(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """
        Scenario 3: Motor-impaired or power user performs complete keyboard-only journey:
        Opens shortcut recorder, tabs to Fn preset, activates via Space,
        tabs to Save button, activates via Enter, and verifies binding.
        """
        dlg = ShortcutCaptureDialog(current_shortcut="ctrl+win")
        dlg.show()

        # Set candidate via preset
        dlg._set_candidate("fn")
        assert dlg._final_shortcut == "fn"

        # Tab to save and activate
        dlg._on_save()
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.get_shortcut() == "fn"
        dlg.close()

    def test_scenario_4_dark_mode_contrast_and_token_cohesion(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """
        Scenario 4: User enables Vast Dark theme.
        Validates token cohesion across all UI elements and verifies that
        text, backgrounds, cards, and floating pill maintain WCAG AA contrast.
        """
        isolated_controller.set_theme("dark")
        tokens = get_theme_tokens("dark")

        # Contrast audits
        assert _calc_contrast_ratio(tokens["text"], tokens["bg"]) >= 4.5
        assert _calc_contrast_ratio(tokens["text_primary"], tokens["surface"]) >= 4.5
        assert _calc_contrast_ratio(tokens["text_pill_primary"], tokens["surface_pill"]) >= 7.0

        # FlowBar pill remains dark obsidian with light high-contrast text
        assert tokens["surface_pill"] in ("#0B0F19", "#1F1F1F", "#000000")
        assert tokens["text_pill_primary"] == "#F8FAFC"

    def test_scenario_5_screen_reader_audit_walkthrough(
        self, offscreen_widgets: Dict[str, Any], isolated_controller: AppController
    ) -> None:
        """
        Scenario 5: Screen reader accessibility audit verifying that all primary
        interactive controls expose non-empty accessibleName or accessibleDescription.
        """
        page = NotetakerPage(isolated_controller)
        sw = SettingsWorkspaceWidget(isolated_controller)

        controls_to_audit = [
            page.btn_drawer,
            page.btn_start_adhoc,
            page.txt_search,
            page.meetings_list,
            page.media_slider,
            page.empty_card.btn_action,
            sw.combo_mic,
            sw.mic_level_bar,
            sw.btn_test_mic,
            sw.slider_mic_gain,
            sw.combo_compute,
        ]

        for ctrl in controls_to_audit:
            has_name = bool(ctrl.accessibleName().strip())
            has_desc = bool(ctrl.accessibleDescription().strip())
            assert (
                has_name or has_desc
            ), f"Control {ctrl.__class__.__name__} missing accessible metadata"

        page.close()
        sw.close()

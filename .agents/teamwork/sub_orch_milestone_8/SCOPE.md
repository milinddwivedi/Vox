# Scope: Milestone 8 (Comprehensive App-Wide UX Polish & Accessibility)

## Mission
Conduct direct root-cause UX improvements and accessibility enhancements across the VOX application shell, settings, history, and command palette to achieve fluid, minimal Wispr Flow parity while preserving all existing functionality and user data.

## Required Features
1. **Centralized Semantic Design Tokens (`src/voxlocal/ui/theme.py`)**:
   - Eliminate hardcoded hex color codes across views.
   - Establish central semantic tokens: `surface_pill`, `border_pill`, `text_pill_primary`, `text_pill_muted`, `accent_emerald`, `accent_coral`, `card_background`, `split_button_fill`.
   - Ensure standard container padding: 16px for cards, 12px for toolbars, 8px for internal button groups.
2. **Polished Empty States & Quick Starts (`src/voxlocal/ui/notetaker_page.py`, snippets, dictionary)**:
   - Replace bare text labels (`"No meetings recorded yet."`) with rich, modern illustration widgets.
   - Provide 1-click quick-start suggestions and sample meeting templates.
3. **Settings UX Polish & Live Mic Input Meter (`src/voxlocal/ui/settings_dialog.py`)**:
   - Implement real-time audio input level deflection meter in Audio Settings when selecting an input device.
   - Replace intimidating raw compute labels (`float16`, `int8`, `int8_float16`) with friendly user options: "Fastest (Lower Memory)", "Balanced", "Maximum Accuracy".
   - Standardize settings page margins and row spacing.
4. **Comprehensive Accessibility & Keyboard Navigation (`src/voxlocal/ui/main_window.py`, `command_palette.py`, etc.)**:
   - Add `setAccessibleName()` and `setAccessibleDescription()` across all custom interactive buttons, chips, waveforms, and list items.
   - Ensure complete keyboard focusability and traversal (Tab, Shift+Tab, Enter, Space, Escape) throughout all application dialogs and palettes.
5. **Preservation of Existing Data & Tests**:
   - Ensure zero disruption to existing SQLite schemas, recordings, snippets, or database models.
   - Ensure all existing unit/UI tests pass without regression.

## File Ownership
- `src/voxlocal/ui/theme.py`
- `src/voxlocal/ui/settings_dialog.py`
- `src/voxlocal/ui/notetaker_page.py`
- `src/voxlocal/ui/main_window.py` (styling and accessibility only)
- `src/voxlocal/ui/command_palette.py` (accessibility only)
- `tests/test_ux_accessibility_regressions.py` or M8 unit tests

## Authoritative Inputs
- `d:\Milind\Voice Assistant\.agents\teamwork\ORIGINAL_REQUEST.md`
- `d:\Milind\Voice Assistant\PROJECT.md`
- `d:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\report.md`
- `d:\Milind\Voice Assistant\.agents\teamwork\explorer_survey_ux\handoff.md`

"""VOX design tokens, Wispr Flow-inspired Light/Dark palettes, QSS sheets, and app icons.

Light mode pairs warm Sand architectural surfaces (#FCFCFB / #FAF9F7 / #EEEBE3)
with crisp dark FlowBar chrome (#000000), Dawn (#F0D7FF), Fathom (#034F46), and
Lumen (#FFFFEB) accents. Dark mode pairs deep Vast obsidian surfaces (#1A1A1A /
#202020 / #30302F) with high-contrast stone typography (#E9E8E1).
"""

from __future__ import annotations

from typing import Dict
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QLabel


class ThemeTokenDict(dict):
    """Dictionary subclass supporting dot-notation attribute lookups."""

    def __getattr__(self, name: str) -> str:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"Theme token '{name}' not found.")

    def __setattr__(self, name: str, value: str) -> None:
        self[name] = value


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


LIGHT_TOKENS: ThemeTokenDict = ThemeTokenDict({
    # Core Wispr Flow Sand Light Surfaces
    "background": "#FCFCFB",
    "bg": "#FCFCFB",
    "surface": "#FFFFFF",
    "surface_secondary": "#FAF9F7",
    "surface_alt": "#FAF9F7",
    "surface_well": "#F5F4F0",
    "surface_elevated": "#F8F7F3",
    "sidebar_bg": "#FAF9F7",
    "sidebar_hover": "#F0EFEA",
    "sidebar_active": "#EEEBE3",
    "ivory": "#FCFCFB",
    "canvas_ivory": "#FAF9F7",
    "surface_canvas": "#FCFCFB",

    # Borders & Hairlines
    "line": "#EEEBE3",
    "border": "#E5E2D9",
    "line_hover": "#D5D0C5",
    "toggle_border": "#C5C0B3",
    "keycap_border": "#D3CED6",

    # Ink & Typography
    "text_primary": "#1A1A1A",
    "text": "#1A1A1A",
    "text_muted": "#736E62",
    "text_secondary": "#5E5B53",
    "text_dim": "#8E897E",
    "text_tertiary": "#71716E",

    # Signature Accents (Dawn, Fathom, Lumen, Coral, Glow, Signal)
    "accent": "#1A1A1A",
    "accent_primary": "#1A1A1A",
    "accent_dawn": "#F0D7FF",
    "accent_dawn_tint": "#FCF7FF",
    "accent_dawn_hover": "#E6BEFF",
    "accent_dawn_text": "#3B1954",
    "accent_fathom": "#034F46",
    "accent_fathom_tint": "#E2F5F2",
    "accent_fathom_hover": "#06665A",
    "accent_lumen": "#FFFFEB",
    "accent_lumen_border": "#E8E6C3",
    "accent_gold": "#926C15",
    "accent_sky": "#034F46",
    "accent_emerald": "#369489",
    "accent_crimson": "#FF5757",
    "accent_amber": "#FFA946",
    "accent_purple": "#A26EC1",
    "signal_pink": "#FFBCF2",

    # Interactive Controls
    "primary_btn_bg": "#1A1A1A",
    "primary_btn_text": "#FCFCFB",
    "primary_btn_border": "transparent",
    "primary_btn_hover": "#30302F",
    "dawn_btn_bg": "#F0D7FF",
    "dawn_btn_text": "#1A1A1A",
    "dawn_btn_border": "#D2ACE9",
    "fathom_btn_bg": "#034F46",
    "fathom_btn_text": "#FCFCFB",
    "fathom_btn_border": "#023D36",
    "fathom_btn_hover": "#06665A",
    "secondary_btn_bg": "#FFFFFF",
    "secondary_btn_text": "#1A1A1A",
    "secondary_btn_border": "#E5E2D9",
    "secondary_btn_hover": "#F5F4F0",
    "glass_bg": "rgba(252, 252, 251, 0.96)",
    "glass_border": "#EEEBE3",

    # Inputs & Tables
    "input_bg": "#FFFFFF",
    "input_border": "#E5E2D9",
    "input_focus_border": "#4D4A42",
    "tab_bg": "transparent",
    "tab_hover": "#F5F4F0",
    "tab_selected_bg": "#FFFFFF",
    "tab_selected_text": "#1A1A1A",
    "tab_selected_border": "#E5E2D9",
    "table_header_bg": "#FAF9F7",
    "table_header_text": "#736E62",
    "table_row_alt": "#FAF9F7",
    "scrollbar_handle": "rgba(115, 110, 98, 0.25)",
    "scrollbar_hover": "rgba(115, 110, 98, 0.45)",

    # FlowBar Pill & Floating Surfaces (FlowBar stays sleek Obsidian Black across Light & Dark)
    "pill_bg": "#000000",
    "pill_resting_bg": "rgba(0, 0, 0, 0.50)",
    "pill_border": "#30302F",
    "pill_text": "#FCFCFB",
    "pill_button_bg": "#4D4A42",
    "chip_bg": "#F5F4F0",
    "chip_text": "#1A1A1A",
    "chip_border": "#EEEBE3",
    "progressbar_bg": "#EEEBE3",

    # Centralized Semantic Tokens (Milestone 8)
    "surface_pill": "#000000",
    "surface_pill_resting": "rgba(0, 0, 0, 0.50)",
    "surface_pill_card": "#1F1F1F",
    "border_pill": "#30302F",
    "border_pill_focus": "#4D4A42",
    "text_pill_primary": "#FCFCFB",
    "text_pill_muted": "#8E897E",
    "accent_coral": "#FF5757",
    "accent_coral_hover": "#E04545",
    "accent_coral_tint": "rgba(255, 87, 87, 0.12)",
    "accent_emerald_tint": "rgba(16, 185, 129, 0.12)",
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
})


DARK_TOKENS: ThemeTokenDict = ThemeTokenDict({
    # Core Wispr Flow Vast Dark Surfaces
    "background": "#1A1A1A",
    "bg": "#1A1A1A",
    "surface": "#202020",
    "surface_secondary": "#262625",
    "surface_alt": "#262625",
    "surface_well": "#141414",
    "surface_elevated": "#30302F",
    "sidebar_bg": "#141414",
    "sidebar_hover": "#262625",
    "sidebar_active": "#30302F",
    "obsidian": "#1A1A1A",
    "canvas_obsidian": "#1A1A1A",
    "surface_canvas": "#202020",

    # Borders & Hairlines
    "line": "#30302F",
    "border": "#383836",
    "line_hover": "#4A4A48",
    "toggle_border": "#5B5B59",
    "keycap_border": "#5B5B59",

    # Ink & Typography
    "text_primary": "#E9E8E1",
    "text": "#E9E8E1",
    "text_muted": "#9D9C98",
    "text_secondary": "#DEDDD7",
    "text_dim": "#71716E",
    "text_tertiary": "#9D9C98",

    # Signature Accents
    "accent": "#F0D7FF",
    "accent_primary": "#F0D7FF",
    "accent_dawn": "#F0D7FF",
    "accent_dawn_tint": "#2A1F33",
    "accent_dawn_hover": "#E6BEFF",
    "accent_dawn_text": "#3B1954",
    "accent_fathom": "#4DAA9D",
    "accent_fathom_tint": "#132D2A",
    "accent_fathom_hover": "#84CDC1",
    "accent_lumen": "#2C2B1E",
    "accent_lumen_border": "#595530",
    "accent_gold": "#D6C5A2",
    "accent_sky": "#84CDC1",
    "accent_emerald": "#4DAA9D",
    "accent_crimson": "#FF5757",
    "accent_amber": "#FFA946",
    "accent_purple": "#D2ACE9",
    "signal_pink": "#FFBCF2",

    # Interactive Controls
    "primary_btn_bg": "#E9E8E1",
    "primary_btn_text": "#1A1A1A",
    "primary_btn_border": "transparent",
    "primary_btn_hover": "#FFFFFF",
    "dawn_btn_bg": "#F0D7FF",
    "dawn_btn_text": "#1A1A1A",
    "dawn_btn_border": "#D2ACE9",
    "fathom_btn_bg": "#0D6156",
    "fathom_btn_text": "#FCFCFB",
    "fathom_btn_border": "#318C7F",
    "fathom_btn_hover": "#12786B",
    "secondary_btn_bg": "#30302F",
    "secondary_btn_text": "#E9E8E1",
    "secondary_btn_border": "#464544",
    "secondary_btn_hover": "#464544",
    "glass_bg": "rgba(26, 26, 26, 0.96)",
    "glass_border": "#30302F",

    # Inputs & Tables
    "input_bg": "#141414",
    "input_border": "#383836",
    "input_focus_border": "#F0D7FF",
    "tab_bg": "transparent",
    "tab_hover": "#262625",
    "tab_selected_bg": "#30302F",
    "tab_selected_text": "#E9E8E1",
    "tab_selected_border": "#464544",
    "table_header_bg": "#202020",
    "table_header_text": "#9D9C98",
    "table_row_alt": "#1D1D1C",
    "scrollbar_handle": "rgba(233, 232, 225, 0.16)",
    "scrollbar_hover": "rgba(233, 232, 225, 0.34)",

    # FlowBar Pill
    "pill_bg": "#000000",
    "pill_resting_bg": "rgba(0, 0, 0, 0.55)",
    "pill_border": "#30302F",
    "pill_text": "#FCFCFB",
    "pill_button_bg": "#4D4A42",
    "chip_bg": "#262625",
    "chip_text": "#E9E8E1",
    "chip_border": "#383836",
    "progressbar_bg": "#30302F",

    # Centralized Semantic Tokens (Milestone 8)
    "surface_pill": "#000000",
    "surface_pill_resting": "rgba(0, 0, 0, 0.55)",
    "surface_pill_card": "#1F1F1F",
    "border_pill": "#30302F",
    "border_pill_focus": "#5B5B59",
    "text_pill_primary": "#FCFCFB",
    "text_pill_muted": "#71716E",
    "accent_coral": "#FF5757",
    "accent_coral_hover": "#E04545",
    "accent_coral_tint": "rgba(255, 87, 87, 0.16)",
    "accent_emerald_tint": "rgba(16, 185, 129, 0.16)",
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
})


def get_theme_tokens(theme: str = "light") -> ThemeTokenDict:
    """Return LIGHT_TOKENS or DARK_TOKENS based on resolved theme string."""
    return DARK_TOKENS if (theme or "light").lower().strip() == "dark" else LIGHT_TOKENS


def get_ui_font(size_px: int = 14, weight: int = 400, serif: bool = False, mono: bool = False) -> QFont:
    """Resolve clean UI sans, editorial serif, or monospace font stacks with graceful fallback."""
    if serif:
        families = ["EB Garamond", "Georgia", "Times New Roman"]
    elif mono:
        families = ["Google Sans Code", "Consolas", "Cascadia Mono"]
    else:
        families = ["Figtree", "Segoe UI Variable Text", "Segoe UI", "Inter"]

    available = set(QFontDatabase.families())
    chosen = next((fam for fam in families if fam in available), families[-1])
    font = QFont(chosen)
    font.setPixelSize(max(10, int(size_px)))
    if weight >= 650:
        font.setWeight(QFont.Weight.Bold)
    elif weight >= 550:
        font.setWeight(QFont.Weight.DemiBold)
    elif weight >= 500:
        font.setWeight(QFont.Weight.Medium)
    else:
        font.setWeight(QFont.Weight.Normal)
    return font


def get_theme_palette(theme: str = "light") -> QPalette:
    """Generate the QPalette matching Wispr-inspired Sand Light or Vast Dark theme."""
    t = get_theme_tokens(theme)
    pal = QPalette()

    bg_color = QColor(t["bg"])
    surface_color = QColor(t["surface"])
    text_color = QColor(t["text"])
    muted_color = QColor(t["text_muted"])
    dim_color = QColor(t["text_dim"])
    highlight_color = QColor(t["accent_fathom"] if (theme or "light").lower() == "light" else t["accent_purple"])

    pal.setColor(QPalette.ColorRole.Window, bg_color)
    pal.setColor(QPalette.ColorRole.WindowText, text_color)
    pal.setColor(QPalette.ColorRole.Base, surface_color)
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(t["surface_secondary"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1A1A1A"))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#FCFCFB"))
    pal.setColor(QPalette.ColorRole.Text, text_color)
    pal.setColor(QPalette.ColorRole.Button, surface_color)
    pal.setColor(QPalette.ColorRole.ButtonText, text_color)
    pal.setColor(QPalette.ColorRole.BrightText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.Highlight, highlight_color)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, muted_color)

    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, dim_color)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, dim_color)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, dim_color)
    return pal


def get_theme_stylesheet(theme: str = "light") -> str:
    """Build the VOX Hub stylesheet matching Wispr Flow's editorial desktop aesthetic."""
    t = get_theme_tokens(theme)
    is_dark = (theme or "light").lower().strip() == "dark"

    return f"""
/* ============================================================
   VOX HUB DESIGN SYSTEM — {'VAST DARK' if is_dark else 'SAND LIGHT'}
   ============================================================ */

QMainWindow, QDialog {{
    background-color: {t['bg']};
    color: {t['text']};
    font-family: 'Figtree', 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
    font-size: 14px;
}}

QWidget {{
    color: {t['text']};
    font-family: 'Figtree', 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
    font-size: 14px;
}}

QScrollArea {{
    background: transparent;
    border: none;
}}

QFrame#hubSidebar {{
    background-color: {t['sidebar_bg']};
    border-right: 1px solid {t['line']};
}}

QPushButton#navItemBtn {{
    background-color: transparent;
    color: {t['text_secondary']};
    border: none;
    border-radius: 8px;
    padding: 8px 12px;
    text-align: left;
    font-size: 13px;
    font-weight: 500;
}}

QPushButton#navItemBtn:hover {{
    background-color: {t['sidebar_hover']};
    color: {t['text']};
}}

QPushButton#navItemBtn[active="true"] {{
    background-color: {t['sidebar_active']};
    color: {t['text']};
    font-weight: 600;
}}

QFrame#voxCard, QFrame#cardSurface {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 12px;
}}

QFrame#voxWellCard {{
    background-color: {t['surface_well']};
    border: 1px solid {t['line']};
    border-radius: 12px;
}}

QFrame#lumenBanner {{
    background-color: {t['accent_lumen']};
    border: 1px solid {t['accent_lumen_border']};
    border-radius: 12px;
}}

QFrame#dawnBanner {{
    background-color: {t['accent_dawn_tint']};
    border: 1px solid {t['accent_purple']};
    border-radius: 12px;
}}

QPushButton {{
    background-color: {t['secondary_btn_bg']};
    color: {t['secondary_btn_text']};
    border: 1px solid {t['secondary_btn_border']};
    border-radius: 8px;
    padding: 7px 14px;
    font-size: 13px;
    font-weight: 550;
}}

QPushButton:hover {{
    background-color: {t['secondary_btn_hover']};
    border-color: {t['line_hover']};
}}

QPushButton#primaryBtn {{
    background-color: {t['primary_btn_bg']};
    color: {t['primary_btn_text']};
    border: none;
    font-weight: 600;
}}

QPushButton#primaryBtn:hover {{
    background-color: {t['primary_btn_hover']};
}}

QPushButton#dawnBtn {{
    background-color: {t['dawn_btn_bg']};
    color: {t['dawn_btn_text']};
    border: 1px solid {t['dawn_btn_border']};
    font-weight: 600;
}}

QPushButton#dawnBtn:hover {{
    background-color: {t['accent_dawn_hover']};
}}

QPushButton#fathomBtn {{
    background-color: {t['fathom_btn_bg']};
    color: {t['fathom_btn_text']};
    border: 1px solid {t['fathom_btn_border']};
    font-weight: 600;
}}

QPushButton#fathomBtn:hover {{
    background-color: {t['fathom_btn_hover']};
}}

QPushButton#dangerBtn {{
    background-color: transparent;
    color: {t['accent_crimson']};
    border: 1px solid {t['accent_crimson']};
}}

QPushButton#dangerBtn:hover {{
    background-color: rgba(255, 87, 87, 0.12);
}}

QPushButton#ghostBtn {{
    background-color: transparent;
    color: {t['text_muted']};
    border: none;
    padding: 5px 8px;
    border-radius: 6px;
}}

QPushButton#ghostBtn:hover {{
    background-color: {t['surface_well']};
    color: {t['text']};
}}

QPushButton#pillTabBtn {{
    background-color: transparent;
    color: {t['text_muted']};
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 500;
}}

QPushButton#pillTabBtn:hover {{
    background-color: {t['tab_hover']};
    color: {t['text']};
}}

QPushButton#pillTabBtn[active="true"] {{
    background-color: {t['tab_selected_bg']};
    color: {t['tab_selected_text']};
    border: 1px solid {t['tab_selected_border']};
    font-weight: 600;
}}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {t['input_bg']};
    color: {t['text']};
    border: 1px solid {t['input_border']};
    border-radius: 8px;
    padding: 7px 10px;
    font-size: 13px;
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border: 1.5px solid {t['input_focus_border']};
}}

QComboBox {{
    padding-right: 24px;
}}

QListWidget, QTableWidget, QTreeWidget {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 10px;
    outline: none;
    gridline-color: {t['line']};
}}

QHeaderView::section {{
    background-color: {t['table_header_bg']};
    color: {t['table_header_text']};
    border: none;
    border-bottom: 1px solid {t['line']};
    padding: 8px 10px;
    font-size: 12px;
    font-weight: 600;
}}

QCheckBox {{
    spacing: 8px;
    color: {t['text']};
}}

QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {t['scrollbar_handle']};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t['scrollbar_hover']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {t['scrollbar_handle']};
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

QToolTip {{
    background-color: {t['tooltip_bg']};
    color: {t['tooltip_text']};
    border: 1px solid {t['tooltip_border']};
    border-radius: 6px;
    padding: 5px 9px;
    font-size: 12px;
}}

QLabel#keycapBadge {{
    background-color: {t['surface']};
    color: {t['text']};
    border: 1px solid {t['keycap_border']};
    border-bottom: 2px solid {t['keycap_border']};
    border-radius: 5px;
    padding: 2px 7px;
    font-family: 'Google Sans Code', 'Consolas', monospace;
    font-size: 11px;
    font-weight: 600;
}}

QLabel#mutedLabel {{
    color: {t['text_muted']};
    font-size: 12px;
}}
"""


def get_floating_pill_stylesheet(theme: str = "light") -> str:
    """FlowBar & ContextMenu stylesheet (FlowBar capsule stays obsidian black for maximum screen legibility)."""
    t = get_theme_tokens(theme)
    return f"""
    QWidget#flowBarContainer {{
        background-color: {t['surface_pill']};
        border: 1.5px solid {t['border_pill']};
        border-radius: 22px;
        color: {t['text_pill_primary']};
    }}
    QMenu {{
        background-color: {t['surface']};
        color: {t['text']};
        border: 1px solid {t['border']};
        border-radius: 10px;
        padding: 6px;
        font-family: 'Figtree', 'Segoe UI', sans-serif;
        font-size: 12.5px;
    }}
    QMenu::item {{
        padding: 7px 22px 7px 12px;
        border-radius: 6px;
        color: {t['text']};
    }}
    QMenu::item:selected {{
        background-color: {t['surface_well']};
        color: {t['text']};
    }}
    QMenu::item:disabled {{
        color: {t['text_dim']};
    }}
    QMenu::separator {{
        height: 1px;
        background: {t['line']};
        margin: 4px 6px;
    }}
    """


def get_dialog_stylesheet(theme: str = "light") -> str:
    """Modal dialog stylesheet matching Wispr-inspired Sand Light or Vast Dark palette."""
    t = get_theme_tokens(theme)
    return f"""
    QDialog {{
        background-color: {t['bg']};
        border: 1px solid {t['line']};
        border-radius: 16px;
        color: {t['text']};
    }}
    QLabel {{
        font-family: 'Figtree', 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
        color: {t['text']};
    }}
    QLabel#onbHeadline, QLabel#onbStepTitle {{
        font-family: 'EB Garamond', 'Georgia', 'Cambria', serif;
        color: {t['text']};
        font-weight: 500;
        letter-spacing: -0.3px;
    }}
    QLabel#onbEyebrow {{
        color: {t['text_muted']};
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 1.4px;
    }}
    QLabel#mutedLabel {{
        color: {t['text_muted']};
        font-size: 12.5px;
    }}
    QLabel#keycapBadge {{
        background-color: {t['surface']};
        color: {t['text']};
        border: 1px solid {t['keycap_border']};
        border-bottom: 2.5px solid {t['keycap_border']};
        border-radius: 7px;
        padding: 4px 10px;
        font-family: 'Google Sans Code', 'Consolas', monospace;
        font-size: 12px;
        font-weight: 700;
    }}
    QFrame#voxCard, QFrame#cardSurface {{
        background-color: {t['surface']};
        border: 1px solid {t['border']};
        border-radius: 14px;
    }}
    QFrame#voxWellCard {{
        background-color: {t['surface_well']};
        border: 1px solid {t['line']};
        border-radius: 14px;
    }}
    QFrame#lumenBanner {{
        background-color: {t['accent_lumen']};
        border: 1px solid {t['accent_lumen_border']};
        border-radius: 12px;
    }}
    QFrame#dawnBanner {{
        background-color: {t['accent_dawn_tint']};
        border: 1px solid {t['accent_purple']};
        border-radius: 12px;
    }}
    QLineEdit, QTextEdit, QPlainTextEdit {{
        background-color: {t['input_bg']};
        border: 1px solid {t['input_border']};
        border-radius: 10px;
        padding: 10px 14px;
        font-size: 13.5px;
        color: {t['text']};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border: 1.5px solid {t['input_focus_border']};
    }}
    QCheckBox {{
        font-size: 12.5px;
        color: {t['text_secondary']};
        font-weight: 500;
        spacing: 8px;
    }}
    QComboBox {{
        background-color: {t['input_bg']};
        border: 1px solid {t['input_border']};
        border-radius: 8px;
        padding: 7px 26px 7px 12px;
        font-size: 12.5px;
        color: {t['text']};
    }}
    QComboBox:hover {{
        border-color: {t['line_hover']};
    }}
    QComboBox QAbstractItemView {{
        background-color: {t['surface']};
        color: {t['text']};
        border: 1px solid {t['border']};
        border-radius: 8px;
        selection-background-color: {t['surface_well']};
        selection-color: {t['text']};
        outline: none;
    }}
    QProgressBar {{
        background-color: {t['progressbar_bg']};
        border: none;
        border-radius: 4px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background-color: {t['accent_fathom']};
        border-radius: 4px;
    }}
    QPushButton {{
        border-radius: 9px;
        font-weight: 600;
        font-size: 13px;
        padding: 8px 16px;
        background-color: {t['secondary_btn_bg']};
        color: {t['secondary_btn_text']};
        border: 1px solid {t['secondary_btn_border']};
    }}
    QPushButton:hover {{
        background-color: {t['secondary_btn_hover']};
    }}
    QPushButton#primaryBtn {{
        background-color: {t['primary_btn_bg']};
        color: {t['primary_btn_text']};
        border: none;
    }}
    QPushButton#primaryBtn:hover {{
        background-color: {t['primary_btn_hover']};
    }}
    QPushButton#dawnBtn {{
        background-color: {t['dawn_btn_bg']};
        color: {t['dawn_btn_text']};
        border: 1px solid {t['dawn_btn_border']};
        font-weight: 600;
    }}
    QPushButton#dawnBtn:hover {{
        background-color: {t['accent_dawn_hover']};
    }}
    QPushButton#fathomBtn {{
        background-color: {t['fathom_btn_bg']};
        color: {t['fathom_btn_text']};
        border: 1px solid {t['fathom_btn_border']};
        font-weight: 600;
    }}
    QPushButton#fathomBtn:hover {{
        background-color: {t['fathom_btn_hover']};
    }}
    QPushButton#ghostBtn {{
        background-color: transparent;
        color: {t['text_muted']};
        border: none;
        padding: 6px 10px;
    }}
    QPushButton#ghostBtn:hover {{
        color: {t['text']};
        background-color: {t['surface_well']};
    }}
    QPushButton#startBtn {{
        background-color: {t['accent_crimson']};
        color: #FFFFFF;
        border: none;
        font-weight: 700;
    }}
    QPushButton#startBtn:hover {{
        background-color: #E04545;
    }}
    QPushButton#cancelBtn {{
        background-color: {t['secondary_btn_bg']};
        color: {t['text_muted']};
        border: 1px solid {t['line']};
    }}
    """


def create_info_badge(tooltip: str) -> QLabel:
    """Create a subtle desktop info tooltip badge."""
    badge = QLabel("i")
    badge.setObjectName("infoBadge")
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setFixedSize(18, 18)
    badge.setToolTip(tooltip)
    badge.setCursor(Qt.CursorShape.WhatsThisCursor)
    badge.setStyleSheet(
        "QLabel#infoBadge {"
        "  color: #736E62;"
        "  border: 1px solid #D5D0C5;"
        "  font-size: 11px;"
        "  font-weight: 600;"
        "  border-radius: 9px;"
        "}"
        "QLabel#infoBadge:hover {"
        "  color: #1A1A1A;"
        "  border-color: #736E62;"
        "}"
    )
    return badge


def create_app_pixmap(state: str = "idle", size: int = 64, theme: str = "light") -> QPixmap:
    """
    Render a high-DPI original VOX waveform squircle emblem adapting to state:
    - 'idle': Crisp obsidian squircle with warm Sand soundbars & subtle Dawn rim
    - 'listening': Emerald/Fathom luminous soundbars
    - 'processing': Warm amber/Dawn pulse soundbars
    - 'recording': Obsidian squircle with Coral (#FF5757) live badge dot
    - 'both': Active listening + Coral badge dot
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    scale = size / 512.0
    margin = 28.0 * scale
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    corner = 112.0 * scale

    bg_grad = QLinearGradient(0, 0, size, size)
    if state == "listening":
        bg_grad.setColorAt(0.0, QColor("#034F46"))
        bg_grad.setColorAt(1.0, QColor("#022E29"))
    elif state == "recording":
        bg_grad.setColorAt(0.0, QColor("#291416"))
        bg_grad.setColorAt(1.0, QColor("#1A0D0E"))
    elif state == "processing":
        bg_grad.setColorAt(0.0, QColor("#2B1F34"))
        bg_grad.setColorAt(1.0, QColor("#1A1420"))
    else:
        bg_grad.setColorAt(0.0, QColor("#1E1E1D"))
        bg_grad.setColorAt(1.0, QColor("#0D0D0C"))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(bg_grad))
    painter.drawRoundedRect(rect, corner, corner)

    # Subtle Dawn / warm sandstone hairline rim
    rim_color = QColor("#F0D7FF" if state in ("idle", "processing") else "#84CDC1" if state == "listening" else "#FF5757")
    rim_color.setAlpha(90)
    painter.setPen(QPen(rim_color, max(1.2, 4.0 * scale)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect, corner, corner)

    # Original VOX 5-bar symmetrical voice emblem inside
    painter.setPen(Qt.PenStyle.NoPen)
    bar_color = QColor(
        "#84CDC1" if state == "listening"
        else "#F0D7FF" if state == "processing"
        else "#FF5757" if state == "recording"
        else "#FCFCFB"
    )
    painter.setBrush(QBrush(bar_color))

    bars = [
        (134.0, 96.0),
        (188.0, 176.0),
        (242.0, 252.0),
        (296.0, 176.0),
        (350.0, 96.0),
    ]
    bar_w = max(2.0, 28.0 * scale)
    center_y = size / 2.0
    for bx, bh in bars:
        h_s = max(4.0, bh * scale)
        x_s = bx * scale
        painter.drawRoundedRect(
            QRectF(x_s, center_y - h_s / 2.0, bar_w, h_s),
            bar_w / 2.0,
            bar_w / 2.0,
        )

    if state in ("recording", "both"):
        dot_center = QPointF(size - 84.0 * scale, 84.0 * scale)
        painter.setPen(QPen(QColor("#FCFCFB"), max(1.5, 6.0 * scale)))
        painter.setBrush(QColor("#FF5757"))
        painter.drawEllipse(dot_center, 34.0 * scale, 34.0 * scale)

    painter.end()
    return pixmap


def create_app_icon(state: str = "idle", size: int = 64, theme: str = "light") -> QIcon:
    """Multi-resolution high-DPI VOX icon across standard Windows icon sizes."""
    icon = QIcon()
    sizes = [16, 24, 32, 48, 64, 128, 256]
    if size not in sizes:
        sizes.append(size)
    for sz in sizes:
        icon.addPixmap(create_app_pixmap(state=state, size=sz, theme=theme))
    return icon

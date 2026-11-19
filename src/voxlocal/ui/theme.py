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
    # Core Nordic Studio Clean Light Surfaces
    "background": "#F8FAFC",
    "bg": "#F8FAFC",
    "surface": "#FFFFFF",
    "surface_secondary": "#F1F5F9",
    "surface_hover": "#F1F5F9",
    "surface_alt": "#F1F5F9",
    "surface_well": "#E2E8F0",
    "surface_elevated": "#F8FAFC",
    "sidebar_bg": "#F8FAFC",
    "sidebar_hover": "#F1F5F9",
    "sidebar_active": "#E2E8F0",
    "ivory": "#F8FAFC",
    "canvas_ivory": "#F1F5F9",
    "surface_canvas": "#F8FAFC",

    # Borders & Hairlines
    "line": "#E2E8F0",
    "border": "#CBD5E1",
    "line_hover": "#94A3B8",
    "toggle_border": "#94A3B8",
    "keycap_border": "#CBD5E1",

    # Ink & Typography (Deep Midnight Obsidian)
    "text_primary": "#0F172A",
    "text": "#0F172A",
    "text_muted": "#64748B",
    "text_secondary": "#475569",
    "text_dim": "#94A3B8",
    "text_tertiary": "#64748B",

    # Signature Accents (Electric Indigo, Sonic Cyan, Cobalt, Crimson)
    "accent": "#4F46E5",
    "accent_primary": "#4F46E5",
    "accent_dawn": "#EEF2FF",
    "accent_dawn_tint": "#F5F3FF",
    "accent_dawn_hover": "#E0E7FF",
    "accent_dawn_text": "#312E81",
    "accent_fathom": "#4F46E5",
    "accent_fathom_tint": "#EEF2FF",
    "accent_fathom_hover": "#4338CA",
    "accent_lumen": "#F0FDF4",
    "accent_lumen_border": "#BBF7D0",
    "accent_gold": "#D97706",
    "accent_sky": "#0284C7",
    "accent_emerald": "#0D9488",
    "accent_crimson": "#EF4444",
    "accent_amber": "#F59E0B",
    "accent_purple": "#6366F1",
    "signal_pink": "#EC4899",

    # Interactive Controls
    "primary_btn_bg": "#0F172A",
    "primary_btn_text": "#FFFFFF",
    "primary_btn_border": "transparent",
    "primary_btn_hover": "#1E293B",
    "dawn_btn_bg": "#EEF2FF",
    "dawn_btn_text": "#312E81",
    "dawn_btn_border": "#C7D2FE",
    "fathom_btn_bg": "#4F46E5",
    "fathom_btn_text": "#FFFFFF",
    "fathom_btn_border": "#4338CA",
    "fathom_btn_hover": "#4338CA",
    "secondary_btn_bg": "#FFFFFF",
    "secondary_btn_text": "#0F172A",
    "secondary_btn_border": "#E2E8F0",
    "secondary_btn_hover": "#F8FAFC",
    "glass_bg": "rgba(248, 250, 252, 0.96)",
    "glass_border": "#E2E8F0",

    # Inputs & Tables
    "input_bg": "#FFFFFF",
    "input_border": "#CBD5E1",
    "input_focus_border": "#4F46E5",
    "tab_bg": "transparent",
    "tab_hover": "#F1F5F9",
    "tab_selected_bg": "#FFFFFF",
    "tab_selected_text": "#0F172A",
    "tab_selected_border": "#CBD5E1",
    "table_header_bg": "#F8FAFC",
    "table_header_text": "#64748B",
    "table_row_alt": "#F8FAFC",
    "scrollbar_handle": "rgba(100, 116, 139, 0.25)",
    "scrollbar_hover": "rgba(100, 116, 139, 0.45)",

    # FlowBar Pill & Floating Surfaces (Obsidian with Electric Indigo/Cyan Rim)
    "pill_bg": "#0B0F19",
    "pill_resting_bg": "rgba(11, 15, 25, 0.65)",
    "pill_border": "#1E293B",
    "pill_text": "#F8FAFC",
    "pill_button_bg": "#1E293B",
    "chip_bg": "#F1F5F9",
    "chip_text": "#0F172A",
    "chip_border": "#E2E8F0",
    "progressbar_bg": "#E2E8F0",

    # Centralized Semantic Tokens
    "surface_pill": "#0B0F19",
    "surface_pill_resting": "rgba(11, 15, 25, 0.65)",
    "surface_pill_card": "#111827",
    "border_pill": "#1E293B",
    "border_pill_focus": "#4F46E5",
    "text_pill_primary": "#F8FAFC",
    "text_pill_muted": "#94A3B8",
    "accent_coral": "#EF4444",
    "accent_coral_hover": "#DC2626",
    "accent_coral_tint": "rgba(239, 68, 68, 0.12)",
    "accent_emerald_tint": "rgba(13, 148, 136, 0.12)",
    "card_background": "#FFFFFF",
    "card_background_alt": "#F8FAFC",
    "split_button_fill": "#E2E8F0",
    "padding_card": "16px",
    "padding_toolbar": "12px",
    "padding_btn_group": "8px",
    "tooltip_bg": "#0F172A",
    "tooltip_text": "#F8FAFC",
    "tooltip_border": "#1E293B",
    "platform_teams": "#6264A7",
    "platform_zoom": "#2D8CFF",
    "platform_meet": "#0F9D58",
    "platform_slack": "#E01E5A",
    "platform_webex": "#00A0D1",
    "obsidian": "#0F172A",
    "canvas_obsidian": "#0F172A",
    "toggle_idle_track": "#CBD5E1",
    "toggle_thumb": "#FFFFFF",
    "toggle_thumb_checked": "#FFFFFF",
    "status_success": "#059669",
    "status_success_bg": "rgba(16, 185, 129, 0.12)",
    "status_success_text": "#047857",
    "status_warning": "#D97706",
    "status_warning_bg": "rgba(245, 158, 11, 0.12)",
    "status_warning_text": "#B45309",
    "status_danger": "#DC2626",
    "status_danger_bg": "rgba(239, 68, 68, 0.12)",
    "status_danger_text": "#B91C1C",
    "status_info": "#0284C7",
    "status_info_bg": "rgba(14, 165, 233, 0.12)",
    "status_info_text": "#0369A1",
    "status_purple": "#7C3AED",
    "status_purple_bg": "rgba(139, 92, 246, 0.12)",
    "status_purple_text": "#6D28D9",
    "status_neutral": "#64748B",
    "status_neutral_bg": "rgba(100, 116, 139, 0.12)",
    "status_neutral_text": "#475569",
    "audio_meter_low": "#10B981",
    "audio_meter_mid": "#3B82F6",
    "audio_meter_peak": "#EF4444",
})


DARK_TOKENS: ThemeTokenDict = ThemeTokenDict({
    # Core Midnight Obsidian Surfaces
    "background": "#0B0F19",
    "bg": "#0B0F19",
    "surface": "#111827",
    "surface_secondary": "#162032",
    "surface_hover": "#162032",
    "surface_alt": "#162032",
    "surface_well": "#070A12",
    "surface_elevated": "#1E293B",
    "sidebar_bg": "#070A12",
    "sidebar_hover": "#162032",
    "sidebar_active": "#1E293B",
    "obsidian": "#0B0F19",
    "canvas_obsidian": "#0B0F19",
    "surface_canvas": "#111827",

    # Borders & Hairlines
    "line": "#1E293B",
    "border": "#283548",
    "line_hover": "#3B4B64",
    "toggle_border": "#4B5563",
    "keycap_border": "#374151",

    # Ink & Typography
    "text_primary": "#F8FAFC",
    "text": "#F8FAFC",
    "text_muted": "#94A3B8",
    "text_secondary": "#CBD5E1",
    "text_dim": "#64748B",
    "text_tertiary": "#94A3B8",

    # Signature Accents
    "accent": "#6366F1",
    "accent_primary": "#6366F1",
    "accent_dawn": "#818CF8",
    "accent_dawn_tint": "#1E1B4B",
    "accent_dawn_hover": "#A5B4FC",
    "accent_dawn_text": "#E0E7FF",
    "accent_fathom": "#38BDF8",
    "accent_fathom_tint": "#082F49",
    "accent_fathom_hover": "#7DD3FC",
    "accent_lumen": "#064E3B",
    "accent_lumen_border": "#065F46",
    "accent_gold": "#FBBF24",
    "accent_sky": "#38BDF8",
    "accent_emerald": "#2DD4BF",
    "accent_crimson": "#EF4444",
    "accent_amber": "#F59E0B",
    "accent_purple": "#818CF8",
    "signal_pink": "#F472B6",

    # Interactive Controls
    "primary_btn_bg": "#F8FAFC",
    "primary_btn_text": "#0B0F19",
    "primary_btn_border": "transparent",
    "primary_btn_hover": "#FFFFFF",
    "dawn_btn_bg": "#6366F1",
    "dawn_btn_text": "#FFFFFF",
    "dawn_btn_border": "#4F46E5",
    "fathom_btn_bg": "#0284C7",
    "fathom_btn_text": "#FFFFFF",
    "fathom_btn_border": "#0369A1",
    "fathom_btn_hover": "#0369A1",
    "secondary_btn_bg": "#1E293B",
    "secondary_btn_text": "#F8FAFC",
    "secondary_btn_border": "#334155",
    "secondary_btn_hover": "#334155",
    "glass_bg": "rgba(11, 15, 25, 0.96)",
    "glass_border": "#1E293B",

    # Inputs & Tables
    "input_bg": "#070A12",
    "input_border": "#283548",
    "input_focus_border": "#6366F1",
    "tab_bg": "transparent",
    "tab_hover": "#162032",
    "tab_selected_bg": "#1E293B",
    "tab_selected_text": "#F8FAFC",
    "tab_selected_border": "#334155",
    "table_header_bg": "#111827",
    "table_header_text": "#94A3B8",
    "table_row_alt": "#0D1322",
    "scrollbar_handle": "rgba(248, 250, 252, 0.16)",
    "scrollbar_hover": "rgba(248, 250, 252, 0.34)",

    # FlowBar Pill
    "pill_bg": "#0B0F19",
    "pill_resting_bg": "rgba(11, 15, 25, 0.65)",
    "pill_border": "#1E293B",
    "pill_text": "#F8FAFC",
    "pill_button_bg": "#1E293B",
    "chip_bg": "#162032",
    "chip_text": "#F8FAFC",
    "chip_border": "#283548",
    "progressbar_bg": "#1E293B",

    # Centralized Semantic Tokens
    "surface_pill": "#0B0F19",
    "surface_pill_resting": "rgba(11, 15, 25, 0.65)",
    "surface_pill_card": "#111827",
    "border_pill": "#1E293B",
    "border_pill_focus": "#6366F1",
    "text_pill_primary": "#F8FAFC",
    "text_pill_muted": "#64748B",
    "accent_coral": "#EF4444",
    "accent_coral_hover": "#DC2626",
    "accent_coral_tint": "rgba(239, 68, 68, 0.16)",
    "accent_emerald_tint": "rgba(13, 148, 136, 0.16)",
    "card_background": "#111827",
    "card_background_alt": "#162032",
    "split_button_fill": "#283548",
    "padding_card": "16px",
    "padding_toolbar": "12px",
    "padding_btn_group": "8px",
    "tooltip_bg": "#070A12",
    "tooltip_text": "#F8FAFC",
    "tooltip_border": "#283548",
    "platform_teams": "#6264A7",
    "platform_zoom": "#2D8CFF",
    "platform_meet": "#0F9D58",
    "platform_slack": "#E01E5A",
    "platform_webex": "#00A0D1",
    "ivory": "#F8FAFC",
    "canvas_ivory": "#F1F5F9",
    "toggle_idle_track": "#334155",
    "toggle_thumb": "#F8FAFC",
    "toggle_thumb_checked": "#0B0F19",
    "status_success": "#10B981",
    "status_success_bg": "rgba(16, 185, 129, 0.16)",
    "status_success_text": "#6EE7B7",
    "status_warning": "#F59E0B",
    "status_warning_bg": "rgba(245, 158, 11, 0.16)",
    "status_warning_text": "#FCD34D",
    "status_danger": "#EF4444",
    "status_danger_bg": "rgba(239, 68, 68, 0.16)",
    "status_danger_text": "#FCA5A5",
    "status_info": "#38BDF8",
    "status_info_bg": "rgba(56, 189, 248, 0.16)",
    "status_info_text": "#7DD3FC",
    "status_purple": "#8B5CF6",
    "status_purple_bg": "rgba(139, 92, 246, 0.16)",
    "status_purple_text": "#C4B5FD",
    "status_neutral": "#94A3B8",
    "status_neutral_bg": "rgba(148, 163, 184, 0.16)",
    "status_neutral_text": "#CBD5E1",
    "audio_meter_low": "#10B981",
    "audio_meter_mid": "#3B82F6",
    "audio_meter_peak": "#EF4444",
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
    Render a high-DPI modern VOX Acoustic V squircle emblem adapting to state:
    - 'idle': Midnight obsidian squircle with Electric Indigo/Sonic Cyan V and soundbars
    - 'listening': Electric Cyan / Sky luminous acoustic V
    - 'processing': Radiant Indigo / Violet pulse acoustic V
    - 'recording': Midnight obsidian squircle with Coral (#EF4444) live badge dot
    - 'both': Active listening + Coral badge dot
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    scale = size / 512.0
    margin = 22.0 * scale
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    corner = 118.0 * scale

    bg_grad = QLinearGradient(0, 0, size, size)
    if state == "listening":
        bg_grad.setColorAt(0.0, QColor("#0A1926"))
        bg_grad.setColorAt(1.0, QColor("#030D14"))
    elif state == "recording":
        bg_grad.setColorAt(0.0, QColor("#220D12"))
        bg_grad.setColorAt(1.0, QColor("#120508"))
    elif state == "processing":
        bg_grad.setColorAt(0.0, QColor("#140E26"))
        bg_grad.setColorAt(1.0, QColor("#080512"))
    else:
        bg_grad.setColorAt(0.0, QColor("#0B0F19"))
        bg_grad.setColorAt(0.5, QColor("#090D16"))
        bg_grad.setColorAt(1.0, QColor("#040711"))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(bg_grad))
    painter.drawRoundedRect(rect, corner, corner)

    # Hairline rim
    rim_grad = QLinearGradient(0, 0, size, size)
    if state == "listening":
        rim_grad.setColorAt(0.0, QColor(6, 182, 212, 160))
        rim_grad.setColorAt(1.0, QColor(14, 165, 233, 100))
    elif state == "recording":
        rim_grad.setColorAt(0.0, QColor(239, 68, 68, 160))
        rim_grad.setColorAt(1.0, QColor(244, 63, 94, 100))
    elif state == "processing":
        rim_grad.setColorAt(0.0, QColor(168, 85, 247, 160))
        rim_grad.setColorAt(1.0, QColor(99, 102, 241, 100))
    else:
        rim_grad.setColorAt(0.0, QColor(99, 102, 241, 140))
        rim_grad.setColorAt(0.5, QColor(56, 189, 248, 90))
        rim_grad.setColorAt(1.0, QColor(6, 182, 212, 115))

    painter.setPen(QPen(QBrush(rim_grad), max(1.2, 3.5 * scale)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect, corner, corner)

    # Dynamic gradient for the V
    v_grad = QLinearGradient(120 * scale, 120 * scale, 390 * scale, 390 * scale)
    if state == "listening":
        v_grad.setColorAt(0.0, QColor("#38BDF8"))
        v_grad.setColorAt(1.0, QColor("#06B6D4"))
    elif state == "recording":
        v_grad.setColorAt(0.0, QColor("#F43F5E"))
        v_grad.setColorAt(1.0, QColor("#EF4444"))
    elif state == "processing":
        v_grad.setColorAt(0.0, QColor("#A855F7"))
        v_grad.setColorAt(1.0, QColor("#6366F1"))
    else:
        v_grad.setColorAt(0.0, QColor("#6366F1"))  # Electric Indigo
        v_grad.setColorAt(0.45, QColor("#3B82F6")) # Cobalt
        v_grad.setColorAt(1.0, QColor("#06B6D4"))  # Sonic Cyan

    stroke_w = max(2.5, 46.0 * scale)
    pen = QPen(QBrush(v_grad), stroke_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    path = QPainterPath()
    path.moveTo(138 * scale, 148 * scale)
    path.lineTo(256 * scale, 362 * scale)
    path.lineTo(374 * scale, 148 * scale)
    painter.drawPath(path)

    # Soundbars inside V
    painter.setPen(Qt.PenStyle.NoPen)
    c_bar_color = QColor(255, 255, 255, 245)
    s_bar_color = QColor(255, 255, 255, 204)

    # Center bar
    painter.setBrush(QBrush(c_bar_color))
    cb_w = max(1.5, 18 * scale)
    cb_h = max(4.0, 105 * scale)
    painter.drawRoundedRect(QRectF((256.0 - 9.0) * scale, 162 * scale, cb_w, cb_h), cb_w / 2.0, cb_w / 2.0)

    # Left & Right bars (draw only if size >= 32 for crispness)
    if size >= 32:
        painter.setBrush(QBrush(s_bar_color))
        sb_w = max(1.2, 14 * scale)
        sb_h = max(3.0, 60 * scale)
        painter.drawRoundedRect(QRectF(208 * scale, 192 * scale, sb_w, sb_h), sb_w / 2.0, sb_w / 2.0)
        painter.drawRoundedRect(QRectF(290 * scale, 192 * scale, sb_w, sb_h), sb_w / 2.0, sb_w / 2.0)

    # Live recording badge dot if recording
    if state in ("recording", "both"):
        dot_center = QPointF(size - 78.0 * scale, 78.0 * scale)
        painter.setPen(QPen(QColor("#FFFFFF"), max(1.5, 6.0 * scale)))
        painter.setBrush(QColor("#EF4444"))
        painter.drawEllipse(dot_center, 30.0 * scale, 30.0 * scale)

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

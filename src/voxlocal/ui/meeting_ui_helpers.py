"""
VOX 2026 Meeting Intelligence UI Helpers & Safe Rendering Primitives.

Provides:
- Safe HTML renderer for structured AI & note text (zero raw Markdown leaking)
- Clean plain-text formatter for clipboard copy
- Responsive non-trapping segmented controls, collapsible cards, and inline feedback pills
- Light/Dark theme adaptive styling via voxlocal.ui.theme tokens
"""

from __future__ import annotations

import html
import re
from typing import Callable, List, Optional, Tuple

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from voxlocal.ui.icons import get_icon
from voxlocal.ui.theme import get_html_font_family, get_theme_tokens

_TS_TOKEN_RE = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")
_ASK_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_ASK_TRAILING_SOURCES_RE = re.compile(
    r"(?:\n\s*---+\s*)?\n\s*(?:#{1,4}\s*|\*\*)(?:Verified\s+Source\s+Citations|Direct\s+Source\s+Evidence|Source\s+Excerpts|Grounded\s+Source\s+Excerpts[^\n]*|Sources|References|Citations)\b:?[\s\S]*$",
    re.IGNORECASE,
)
_ASK_INFO_TIP_RE = re.compile(r"\n?>\s*ℹ️\s*\*?Click any citation[^\n]*\*?", re.IGNORECASE)
_ASK_BRACKET_TS_RE = re.compile(r"\(\s*\[\d{1,2}:\d{2}(?::\d{2})?(?:\s*[·|]\s*[^\]]+)?\]\s*\)|\[\d{1,2}:\d{2}(?::\d{2})?(?:\s*[·|]\s*[^\]]+)?\]")
_ASK_BACKTICK_TS_RE = re.compile(r"`\s*\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*`")
_ASK_REF_TAG_RE = re.compile(r"\[\d{1,2}\](?!\()")


def sanitize_ask_vox_display_markdown(text: str) -> str:
    """
    Strip technical source blocks, citation footers, bracketed [MM:SS]/[1] tags,
    and <think> reasoning fragments from Ask VOX responses while preserving clean
    conversational Markdown structure (headings, lists, tables, code, bold emphasis).
    """
    raw = str(text or "")
    if not raw.strip():
        return ""
    s = _ASK_THINK_BLOCK_RE.sub("", raw)
    if "<think>" in s.lower():
        idx = s.lower().find("<think>")
        s = s[:idx]
    s = _ASK_TRAILING_SOURCES_RE.sub("", s)
    s = _ASK_INFO_TIP_RE.sub("", s)

    cleaned_lines: List[str] = []
    in_code_fence = False
    for line in s.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            cleaned_lines.append(line)
            continue
        if not in_code_fence:
            line = _ASK_BRACKET_TS_RE.sub("", line)
            line = _ASK_BACKTICK_TS_RE.sub("", line)
            line = _ASK_REF_TAG_RE.sub("", line)
            line = re.sub(r"[ \t]{2,}", " ", line)
            line = re.sub(r"\s+([.,;:?!])", r"\1", line)
            line = re.sub(r"\(\s*\)", "", line)
            line = line.rstrip()
        cleaned_lines.append(line)

    out = "\n".join(cleaned_lines).strip()
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


def parse_timestamp_seconds(ts_str: str) -> float:
    parts = [p for p in str(ts_str or "").strip().split(":") if p]
    try:
        if len(parts) == 2:
            return float(int(parts[0]) * 60 + int(parts[1]))
        if len(parts) == 3:
            return float(int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]))
    except Exception:
        pass
    return 0.0


def _apply_inline_markdown_html(escaped_line: str, t: dict, include_timestamp_chips: bool = True) -> str:
    """Apply safe inline Markdown formatting to an already HTML-escaped line."""
    accent = t.get("accent_fathom", "#4F46E5")
    chip_bg = t.get("chip_bg", "#F1F5F9")
    chip_bdr = t.get("chip_border", "#CBD5E1")
    code_bg = t.get("surface_well", "#E2E8F0")

    # Inline code `...` (only well-formed inline spans), then strip stray dangling backticks
    s = re.sub(
        r"`([^`]+)`",
        lambda m: f'<code style="background:{code_bg};padding:1px 5px;border-radius:4px;font-family:Consolas,monospace;font-size:90%;">{m.group(1)}</code>',
        escaped_line,
    )
    s = s.replace("`", "")
    # Bold **...** and __...__
    s = re.sub(r"\*\*([^\*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", s)
    # Italics *...*
    s = re.sub(r"(?<!\*)\*([^\*]+)\*(?!\*)", r"<em>\1</em>", s)
    # Markdown links [label](url)
    s = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        lambda m: f'<a href="{m.group(2)}" style="color:{accent};text-decoration:underline;">{m.group(1)}</a>',
        s,
    )

    if include_timestamp_chips:
        # Clickable [MM:SS] timestamps (Summary / Overview workspace)
        def _ts_badge(m: re.Match) -> str:
            lbl = m.group(1)
            sec = parse_timestamp_seconds(lbl)
            return (
                f'<a href="seek:{sec:.1f}" '
                f'style="background:{chip_bg};color:{accent};border:1px solid {chip_bdr};'
                f'border-radius:4px;padding:0px 5px;font-weight:700;text-decoration:none;font-size:11px;">'
                f"{lbl}</a>"
            )

        s = _TS_TOKEN_RE.sub(_ts_badge, s)
    return s


def render_safe_markdown_html(text: str, theme: str = "light", include_timestamp_chips: bool = True) -> str:
    """
    Convert structured or AI-generated Markdown/plain text into clean, safe HTML
    without exposing raw '#', '**', or '- [ ]' syntax in the Qt UI.
    """
    t = get_theme_tokens(theme)
    txt = str(text or "").strip()
    if not txt:
        return ""

    fg = t.get("text_primary", "#0F172A")
    muted = t.get("text_secondary", "#475569")
    border = t.get("border", "#CBD5E1")
    tbl_hdr_bg = t.get("table_header_bg", "#F8FAFC")
    code_bg = t.get("surface_container", "#F1F5F9")

    lines = txt.splitlines()
    font_css = get_html_font_family()
    out: List[str] = [
        f'<div style="font-family:{font_css};font-size:13.5px;line-height:1.62;color:{fg};overflow-wrap:anywhere;">'
    ]
    in_ul = False
    in_ol = False
    in_table = False
    table_header_done = False
    in_code_block = False
    code_lines: List[str] = []

    def _close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def _close_table() -> None:
        nonlocal in_table, table_header_done
        if in_table:
            out.append("</tbody></table>")
            in_table = False
            table_header_done = False

    def _flush_code_block() -> None:
        nonlocal in_code_block, code_lines
        if in_code_block:
            joined = html.escape("\n".join(code_lines))
            out.append(
                f'<pre style="background:{code_bg};border:1px solid {border};border-radius:8px;'
                f'padding:8px 10px;margin:6px 0;font-family:Consolas,monospace;font-size:12px;'
                f'white-space:pre-wrap;"><code>{joined}</code></pre>'
            )
            code_lines = []
            in_code_block = False

    for raw_ln in lines:
        s = raw_ln.strip()
        if s.startswith("```"):
            _close_lists()
            _close_table()
            if in_code_block:
                _flush_code_block()
            else:
                in_code_block = True
                code_lines = []
            continue

        if in_code_block:
            code_lines.append(raw_ln.rstrip("\n"))
            continue

        if not s:
            _close_lists()
            _close_table()
            out.append('<div style="height:6px;"></div>')
            continue

        # Horizontal rules / ASCII divider lines
        if re.fullmatch(r"[-=_]{3,}", s):
            _close_lists()
            _close_table()
            out.append(f'<hr style="border:none;border-top:1px solid {border};margin:8px 0;">')
            continue

        # Pipe tables
        if s.startswith("|") and s.endswith("|"):
            _close_lists()
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c or "---") for c in cells):
                continue
            if not in_table:
                in_table = True
                table_header_done = False
                out.append(
                    f'<table style="width:100%;border-collapse:collapse;margin:8px 0;font-size:12px;">'
                )
            if not table_header_done:
                out.append("<thead><tr>")
                for c in cells:
                    out.append(
                        f'<th style="text-align:left;padding:6px 8px;background:{tbl_hdr_bg};'
                        f'border-bottom:1px solid {border};color:{muted};font-weight:700;">'
                        f"{_apply_inline_markdown_html(html.escape(c), t, include_timestamp_chips=include_timestamp_chips)}</th>"
                    )
                out.append("</tr></thead><tbody>")
                table_header_done = True
            else:
                out.append("<tr>")
                for c in cells:
                    out.append(
                        f'<td style="padding:6px 8px;border-bottom:1px solid {border};vertical-align:top;">'
                        f"{_apply_inline_markdown_html(html.escape(c), t, include_timestamp_chips=include_timestamp_chips)}</td>"
                    )
                out.append("</tr>")
            continue

        _close_table()

        # Headings (# .. ####)
        m_h = re.match(r"^(#{1,4})\s+(.+)$", s)
        if m_h:
            _close_lists()
            lvl = len(m_h.group(1))
            fsize = {1: "16.5px", 2: "15px", 3: "14px", 4: "13.5px"}.get(lvl, "13.5px")
            top_m = "10px" if lvl <= 2 else "7px"
            out.append(
                f'<div style="font-size:{fsize};font-weight:600;color:{fg};margin:{top_m} 0 4px 0;">'
                f"{_apply_inline_markdown_html(html.escape(m_h.group(2)), t, include_timestamp_chips=include_timestamp_chips)}</div>"
            )
            continue

        # Checklists (- [ ] / - [x])
        m_chk = re.match(r"^[-*•]\s+\[([ xX])\]\s+(.+)$", s)
        if m_chk:
            _close_lists()
            checked = m_chk.group(1).lower() == "x"
            mark = f'<span style="color:{t.get("status_success", "#10B981")};font-weight:700;">☑</span>' if checked else f'<span style="color:{muted};">☐</span>'
            txt_style = f"color:{muted};text-decoration:line-through;" if checked else f"color:{fg};"
            out.append(
                f'<div style="margin:3px 0 3px 4px;{txt_style}">{mark} '
                f"{_apply_inline_markdown_html(html.escape(m_chk.group(2)), t, include_timestamp_chips=include_timestamp_chips)}</div>"
            )
            continue

        # Bullet lists
        m_ul = re.match(r"^(?:[-*•])\s+(.+)$", s)
        if m_ul:
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if not in_ul:
                out.append('<ul style="margin:4px 0 4px 18px;padding-left:0;">')
                in_ul = True
            out.append(
                f'<li style="margin-bottom:4px;">{_apply_inline_markdown_html(html.escape(m_ul.group(1)), t, include_timestamp_chips=include_timestamp_chips)}</li>'
            )
            continue

        # Numbered lists
        m_ol = re.match(r"^(\d+)[.)]\s+(.+)$", s)
        if m_ol:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if not in_ol:
                out.append('<ol style="margin:4px 0 4px 18px;padding-left:0;">')
                in_ol = True
            out.append(
                f'<li style="margin-bottom:4px;">{_apply_inline_markdown_html(html.escape(m_ol.group(2)), t, include_timestamp_chips=include_timestamp_chips)}</li>'
            )
            continue

        # Plain paragraph
        _close_lists()
        out.append(
            f'<p style="margin:0 0 6px 0;">{_apply_inline_markdown_html(html.escape(s), t, include_timestamp_chips=include_timestamp_chips)}</p>'
        )

    _flush_code_block()
    _close_lists()
    _close_table()
    out.append("</div>")
    return "".join(out)


def strip_markdown_for_plain_copy(text: str) -> str:
    """Convert Markdown text cleanly to readable plain text with zero raw artifacts."""
    out_lines: List[str] = []
    for raw_ln in str(text or "").splitlines():
        s = raw_ln.rstrip()
        if re.fullmatch(r"\s*[-=_]{3,}\s*", s):
            continue
        s = re.sub(r"^\s*#{1,6}\s+", "", s)
        s = re.sub(r"^\s*[-*]\s+\[[xX]\]\s+", "☑ ", s)
        s = re.sub(r"^\s*[-*]\s+\[\s\]\s+", "☐ ", s)
        s = re.sub(r"^\s*[-*]\s+", "• ", s)
        s = re.sub(r"\*\*([^\*]+)\*\*", r"\1", s)
        s = re.sub(r"__([^_]+)__", r"\1", s)
        s = re.sub(r"(?<!\*)\*([^\*]+)\*(?!\*)", r"\1", s)
        s = re.sub(r"`([^`]+)`", r"\1", s)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", s)
        out_lines.append(s)
    return "\n".join(out_lines).strip()


def split_markdown_sections(markdown_text: str) -> List[Tuple[str, str]]:
    """Split markdown into (section_title, section_markdown) pairs on ##/###/#### headings."""
    lines = str(markdown_text or "").splitlines()
    sections: List[Tuple[str, List[str]]] = []
    cur_title = "Full Response"
    cur_lines: List[str] = []
    for ln in lines:
        m = re.match(r"^\s*#{2,4}\s+(.+?)\s*$", ln)
        if m:
            if cur_lines and any(x.strip() for x in cur_lines):
                sections.append((cur_title, cur_lines))
            cur_title = re.sub(r"[*_`]+", "", m.group(1)).strip()
            cur_lines = [ln]
        else:
            cur_lines.append(ln)
    if cur_lines and any(x.strip() for x in cur_lines):
        sections.append((cur_title, cur_lines))
    return [(title, "\n".join(body).strip()) for title, body in sections if any(x.strip() for x in body)]


class WheelForwardingFilter(QObject):
    """Forward wheel events from nested display widgets to parent scroll area so scrolling never traps."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel:
            w = obj if isinstance(obj, QWidget) else None
            if w is not None and w.parentWidget() is not None:
                event.ignore()
                return True
        return super().eventFilter(obj, event)


class VoxSegmentedControl(QFrame):
    """Compact 2026 segmented pill selector (e.g., Brief | Detailed)."""

    selection_changed = pyqtSignal(str)

    def __init__(
        self,
        options: List[Tuple[str, str]],
        parent: Optional[QWidget] = None,
        theme: str = "light",
    ) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("voxSegmentedControl")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._options = list(options)
        self._buttons: dict[str, QPushButton] = {}
        self._current_key = options[0][0] if options else ""

        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)

        for key, label in self._options:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.set_current(k, emit=True))
            lay.addWidget(btn)
            self._buttons[key] = btn

        self.set_current(self._current_key, emit=False)
        self.set_theme(self.theme)

    def current_key(self) -> str:
        return self._current_key

    def set_current(self, key: str, emit: bool = False) -> None:
        if key not in self._buttons:
            return
        changed = (self._current_key != key)
        self._current_key = key
        for k, btn in self._buttons.items():
            btn.setChecked(k == key)
        self.set_theme(self.theme)
        if emit and changed:
            self.selection_changed.emit(key)

    def set_theme(self, theme_name: str) -> None:
        self.theme = (theme_name or "light").lower()
        t = get_theme_tokens(self.theme)
        self.setStyleSheet(
            f"QFrame#voxSegmentedControl {{ background-color: {t['surface_well']}; "
            f"border: 1px solid {t['border']}; border-radius: 9px; }}"
        )
        for k, btn in self._buttons.items():
            active = (k == self._current_key)
            if active:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: {t['surface']}; color: {t['accent_dawn_text']}; "
                    f"border: 1px solid {t['outline_variant']}; border-radius: 7px; padding: 4px 12px; "
                    f"font-size: 12.5px; font-weight: 600; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: transparent; color: {t['text_secondary']}; "
                    f"border: 1px solid transparent; border-radius: 7px; padding: 4px 12px; "
                    f"font-size: 12.5px; font-weight: 500; }} "
                    f"QPushButton:hover {{ color: {t['text_primary']}; }}"
                )


class VoxCollapsibleSectionCard(QFrame):
    """Editorial section block supporting standalone card or flat embedded section inside a unified document card."""

    copy_requested = pyqtSignal()

    def __init__(
        self,
        title: str,
        *,
        section_id: str = "",
        show_copy: bool = True,
        copy_label: str = "Copy",
        collapsed: bool = False,
        flat_embedded: bool = False,
        parent: Optional[QWidget] = None,
        theme: str = "light",
    ) -> None:
        super().__init__(parent)
        self.theme = theme
        self.section_id = section_id
        self._collapsed = bool(collapsed)
        self._flat_embedded = bool(flat_embedded)
        self._copy_feedback_state = "idle"
        self.setObjectName("voxFlatSection" if self._flat_embedded else "voxSectionCard")
        self.setMinimumWidth(0)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 0, 12) if self._flat_embedded else root.setContentsMargins(16, 12, 16, 14)
        root.setSpacing(6 if self._flat_embedded else 10)

        hdr = QHBoxLayout()
        hdr.setSpacing(6 if self._flat_embedded else 8)

        self.btn_toggle = QPushButton("")
        self.btn_toggle.setFixedSize(26, 26)
        self.btn_toggle.setIconSize(QSize(14, 14))
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle.setToolTip("Expand or collapse section")
        self.btn_toggle.setAccessibleName(f"Toggle {title} section")
        self.btn_toggle.clicked.connect(self.toggle_collapsed)
        if self._flat_embedded:
            self.btn_toggle.setVisible(False)
        hdr.addWidget(self.btn_toggle)

        self.lbl_title = QLabel(title)
        self.lbl_title.setMinimumWidth(0)
        self.lbl_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        hdr.addWidget(self.lbl_title, 1)

        self.lbl_badge = QLabel("")
        self.lbl_badge.setVisible(False)
        hdr.addWidget(self.lbl_badge)

        self._header_extra_layout = QHBoxLayout()
        self._header_extra_layout.setSpacing(6)
        hdr.addLayout(self._header_extra_layout)

        self.btn_copy: Optional[QPushButton] = None
        self._copy_reset_timer = QTimer(self)
        self._copy_reset_timer.setSingleShot(True)
        self._copy_reset_timer.setInterval(1600)
        self._copy_reset_timer.timeout.connect(self._reset_copy_icon)
        if show_copy:
            self.btn_copy = QPushButton("")
            self.btn_copy.setFixedSize(26, 26)
            self.btn_copy.setIconSize(QSize(13, 13))
            self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_copy.setToolTip(copy_label or f"Copy {title}")
            self.btn_copy.setAccessibleName(copy_label or f"Copy {title}")
            self.btn_copy.clicked.connect(self.copy_requested.emit)
            hdr.addWidget(self.btn_copy)

        root.addLayout(hdr)

        self.body_widget = QWidget()
        self.body_widget.setMinimumWidth(0)
        self.body_layout = QVBoxLayout(self.body_widget)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(6 if self._flat_embedded else 8)
        self.body_widget.setVisible(not self._collapsed)
        root.addWidget(self.body_widget)

        self.set_theme(self.theme)

    def show_copied_feedback(self) -> None:
        self._copy_feedback_state = "copied"
        if self.btn_copy is not None:
            t = get_theme_tokens(self.theme)
            self.btn_copy.setIcon(get_icon("check", t.get("status_success_text", "#059669"), 13))
            self._copy_reset_timer.start()

    def _reset_copy_icon(self) -> None:
        self._copy_feedback_state = "idle"
        if self.btn_copy is not None:
            t = get_theme_tokens(self.theme)
            self.btn_copy.setIcon(get_icon("copy", t.get("text_secondary", "#475569"), 13 if self._flat_embedded else 14))

    def add_header_widget(self, widget: QWidget) -> None:
        self._header_extra_layout.addWidget(widget)

    def set_count_badge(self, text: str) -> None:
        clean = str(text or "").strip()
        self.lbl_badge.setText(clean)
        self.lbl_badge.setVisible(bool(clean) and not self._flat_embedded)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self.body_widget.setVisible(not self._collapsed)
        t = get_theme_tokens(self.theme)
        self.btn_toggle.setIcon(
            get_icon("chevron_right" if self._collapsed else "chevron_down", t.get("text_secondary", "#475569"), 14)
        )

    def is_collapsed(self) -> bool:
        return self._collapsed

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_theme(self, theme_name: str) -> None:
        self.theme = (theme_name or "light").lower()
        t = get_theme_tokens(self.theme)
        if self._flat_embedded:
            self.setStyleSheet(
                f"QFrame#voxFlatSection {{ background: transparent; border: none; border-bottom: 1px solid {t['outline_variant']}; }}"
            )
            self.lbl_title.setStyleSheet(
                f"font-size: 13.5px; font-weight: 600; color: {t['text_primary']}; border: none; background: transparent;"
            )
        else:
            self.setStyleSheet(
                f"QFrame#voxSectionCard {{ background-color: {t['surface']}; "
                f"border: 1px solid {t['outline_variant']}; border-radius: 12px; }}"
            )
            self.lbl_title.setStyleSheet(
                f"font-size: 14.5px; font-weight: 600; color: {t['text_primary']}; border: none; background: transparent;"
            )
        self.lbl_badge.setStyleSheet(
            f"background: {t['surface_container']}; color: {t['text_secondary']}; border: none; "
            f"border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: 600;"
        )
        self.btn_toggle.setIcon(
            get_icon("chevron_right" if self._collapsed else "chevron_down", t.get("text_secondary", "#475569"), 14)
        )
        self.btn_toggle.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; border-radius: 6px; padding: 0px; }} "
            f"QPushButton:hover {{ background: {t['surface_hover']}; }}"
        )
        if self.btn_copy is not None:
            icon_name = "check" if self._copy_feedback_state == "copied" else "copy"
            icon_col = (
                t.get("status_success_text", "#059669")
                if self._copy_feedback_state == "copied"
                else t.get("text_secondary", "#475569")
            )
            self.btn_copy.setIcon(get_icon(icon_name, icon_col, 13 if self._flat_embedded else 14))
            self.btn_copy.setStyleSheet(
                f"QPushButton {{ background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 0px; }} "
                f"QPushButton:hover {{ background: {t['surface_hover']}; border-color: {t['outline_variant']}; }}"
            )


class VoxInlineFeedbackPill(QLabel):
    """Non-intrusive temporary status pill confirming Copy / Export / Save feedback."""

    def __init__(self, parent: Optional[QWidget] = None, theme: str = "light") -> None:
        super().__init__("", parent)
        self.theme = theme
        self.setVisible(False)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.setVisible(False))

    def show_message(self, text: str, kind: str = "success", duration_ms: int = 3200) -> None:
        t = get_theme_tokens(self.theme)
        if kind == "error":
            bg, fg, bdr = t["status_danger_bg"], t["status_danger_text"], t["status_danger"]
        elif kind == "warning":
            bg, fg, bdr = t["status_warning_bg"], t["status_warning_text"], t["status_warning"]
        else:
            bg, fg, bdr = t["status_success_bg"], t["status_success_text"], t["status_success"]
        self.setStyleSheet(
            f"background: {bg}; color: {fg}; border: 1px solid {bdr}; "
            f"border-radius: 8px; padding: 3px 10px; font-size: 11.5px; font-weight: 600;"
        )
        self.setText(text)
        self.setVisible(True)
        self._timer.start(max(1000, int(duration_ms)))

"""Command Palette (Ctrl+K) quick switcher and action overlay for VOX."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from voxlocal.ui.theme import get_theme_tokens


@dataclass
class PaletteCommand:
    """Represents an executable action within the Command Palette."""
    id: str
    title: str
    description: str
    category: str
    shortcut: str = ""
    handler: Optional[Callable[[], None]] = None


class CommandItemWidget(QWidget):
    """Custom widget rendered for each entry in the Command Palette."""

    def __init__(self, command: PaletteCommand, theme: str = "dark", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.command = command
        self._theme = theme

        t = get_theme_tokens(theme)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        is_dark = (self._theme or "").lower() == "dark"
        cat_fg = t.get("accent_dawn", "#F0D7FF") if is_dark else t.get("accent_fathom", "#034F46")
        cat_bg = t.get("accent_dawn_tint", "#2A1F33") if is_dark else t.get("accent_fathom_tint", "#E2F5F2")

        # Category chip
        lbl_cat = QLabel(command.category.upper())
        lbl_cat.setStyleSheet(
            f"font-size: 10px; font-weight: 800; color: {cat_fg}; "
            f"background: {cat_bg}; border-radius: 4px; padding: 2px 6px;"
        )
        layout.addWidget(lbl_cat)

        # Title and Description
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self.lbl_title = QLabel(command.title)
        self.lbl_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {t.get('text_primary', '#F4F4F5')};")
        self.lbl_title.setAccessibleName(command.title)
        text_col.addWidget(self.lbl_title)

        if command.description:
            self.lbl_desc = QLabel(command.description)
            self.lbl_desc.setStyleSheet(f"font-size: 11px; color: {t.get('text_muted', '#8B949E')};")
            self.lbl_desc.setAccessibleName(command.description)
            text_col.addWidget(self.lbl_desc)

        layout.addLayout(text_col, 1)

        # Shortcut hint
        if command.shortcut:
            lbl_sc = QLabel(command.shortcut)
            lbl_sc.setStyleSheet(
                f"font-size: 11px; font-family: monospace; font-weight: 600; "
                f"color: {t.get('text_dim', '#8E897E')}; "
                f"background: {t.get('surface_well', 'rgba(255, 255, 255, 0.08)')}; border: 1px solid {t.get('line', 'rgba(255, 255, 255, 0.12)')}; "
                f"border-radius: 4px; padding: 2px 6px;"
            )
            layout.addWidget(lbl_sc)

        self.setAccessibleName(f"{command.category}: {command.title}")
        self.setAccessibleDescription(
            f"{command.description}. Shortcut: {command.shortcut}" if command.shortcut else (command.description or "")
        )


class VoxCommandPaletteDialog(QDialog):
    """
    Floating Command Palette (Ctrl+K) quick switcher overlay.
    Provides instant keyboard-driven search and navigation across all
    5 primary workspaces and key desktop actions.
    """

    command_executed = pyqtSignal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        theme: str = "dark",
        commands: Optional[List[PaletteCommand]] = None,
        controller: Optional[Any] = None,
    ) -> None:
        super().__init__(parent)
        self._theme = (theme or "dark").lower()
        self._controller = controller
        self._all_commands: List[PaletteCommand] = commands or []
        self._filtered_commands: List[PaletteCommand] = []

        self.setWindowTitle("VOX Command Palette")
        self.setModal(True)
        self.setFixedSize(580, 420)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._build_ui()
        self.set_commands(self._all_commands)
        self.apply_theme(self._theme)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        # Container card with rounded borders and elevated shadow
        self.container = QFrame()
        self.container.setObjectName("paletteContainer")
        c_layout = QVBoxLayout(self.container)
        c_layout.setContentsMargins(16, 16, 16, 16)
        c_layout.setSpacing(12)

        # Search Bar
        search_row = QHBoxLayout()
        search_row.setSpacing(10)

        lbl_icon = QLabel("🔍")
        lbl_icon.setStyleSheet("font-size: 15px;")
        search_row.addWidget(lbl_icon)

        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("paletteSearchInput")
        self.txt_search.setPlaceholderText("Type a command or jump to workspace... (e.g. Dictation, Meetings, Record, Theme)")
        self.txt_search.setAccessibleName("Command search input")
        self.txt_search.setAccessibleDescription("Type a command, action, or workspace name to filter list")
        self.txt_search.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.txt_search, 1)

        self.lbl_esc = QLabel("ESC to close")
        self.lbl_esc.setAccessibleName("Escape key dismissal hint")
        self.lbl_esc.setAccessibleDescription("Press Escape key to dismiss the command palette")
        self.lbl_esc.setStyleSheet("font-size: 10px; color: #8B949E; border: 1px solid rgba(255,255,255,0.15); border-radius: 3px; padding: 2px 4px;")
        search_row.addWidget(self.lbl_esc)
        c_layout.addLayout(search_row)

        # Divider line
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("background: rgba(255, 255, 255, 0.08); min-height: 1px; max-height: 1px;")
        c_layout.addWidget(divider)

        # Results List
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("paletteList")
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setAccessibleName("Command suggestions list")
        self.list_widget.setAccessibleDescription("Matching commands and actions. Navigate with Up and Down arrows, execute with Enter or Space, close with Escape.")
        self.list_widget.itemActivated.connect(self._on_item_activated)
        c_layout.addWidget(self.list_widget, 1)

        # Footer Hint
        footer = QHBoxLayout()
        self.lbl_hint = QLabel("↑/↓ Navigate   •   ↵ Execute   •   ESC Dismiss")
        self.lbl_hint.setAccessibleName("Keyboard navigation shortcuts hint")
        self.lbl_hint.setAccessibleDescription("Use Up and Down arrows to navigate, Enter or Space to execute, and Escape to dismiss")
        self.lbl_hint.setStyleSheet("font-size: 10.5px; color: #8B949E; font-weight: 500;")
        footer.addWidget(self.lbl_hint)
        footer.addStretch()
        c_layout.addLayout(footer)

        self.setTabOrder(self.txt_search, self.list_widget)
        root.addWidget(self.container)

    def set_commands(self, commands: List[PaletteCommand]) -> None:
        """Update registered commands and refresh display."""
        self._all_commands = commands
        self._filter_commands(self.txt_search.text().strip())

    def apply_theme(self, theme: str) -> None:
        """Apply theme styling to dialog container and search elements."""
        self._theme = theme.lower()
        t = get_theme_tokens(theme)

        bg = t.get("surface", "#161B22")
        border = t.get("line", "rgba(255, 255, 255, 0.12)")
        text = t.get("text_primary", "#F4F4F5")
        input_bg = t.get("input_bg", "#12161D")
        focus_border = t.get("accent_dawn", "#F0D7FF") if self._theme == "dark" else t.get("accent_fathom", "#034F46")
        sel_bg = t.get("sidebar_active", "rgba(59, 130, 246, 0.16)")

        self.container.setStyleSheet(
            f"QFrame#paletteContainer {{ "
            f"  background-color: {bg}; "
            f"  border: 1.5px solid {border}; "
            f"  border-radius: 14px; "
            f"}}"
        )
        self.txt_search.setStyleSheet(
            f"QLineEdit#paletteSearchInput {{ "
            f"  background-color: {input_bg}; "
            f"  border: 1px solid {border}; "
            f"  border-radius: 8px; "
            f"  color: {text}; "
            f"  font-size: 13.5px; "
            f"  padding: 8px 12px; "
            f"}}"
            f"QLineEdit#paletteSearchInput:focus {{ "
            f"  border-color: {focus_border}; "
            f"}}"
        )
        self.list_widget.setStyleSheet(
            f"QListWidget#paletteList {{ "
            f"  background: transparent; "
            f"  border: none; "
            f"}} "
            f"QListWidget#paletteList::item {{ "
            f"  border-radius: 8px; "
            f"  margin-bottom: 2px; "
            f"}} "
            f"QListWidget#paletteList::item:selected {{ "
            f"  background-color: {sel_bg}; "
            f"}}"
        )

        # Refresh visible list items with theme tokens
        self._render_items()

    def _on_search_changed(self, text: str) -> None:
        self._filter_commands(text.strip())

    def _filter_commands(self, query: str) -> None:
        q = query.lower()
        if not q:
            self._filtered_commands = list(self._all_commands)
        else:
            self._filtered_commands = [
                cmd for cmd in self._all_commands
                if q in cmd.title.lower() or q in cmd.description.lower() or q in cmd.category.lower()
            ]
            if self._controller and hasattr(self._controller, "db") and self._controller.db:
                db_matches = self._search_database_matches(query)
                self._filtered_commands.extend(db_matches)
        self._render_items()

    def _search_database_matches(self, query: str) -> List[PaletteCommand]:
        matches: List[PaletteCommand] = []
        db = getattr(self._controller, "db", None)
        if not db:
            return matches
        try:
            for d in db.list_dictation_history(search_query=query, limit=3):
                preview = str(d.get("final_text") or d.get("raw_text") or "").replace("\n", " ").strip()[:60]
                if preview:
                    matches.append(PaletteCommand(
                        id="home",
                        title=preview,
                        description="Dictation record",
                        category="Dictation",
                        shortcut="",
                    ))
            for m in db.list_meetings(search_query=query, limit=3):
                title = m.get("title") or "Untitled Meeting"
                matches.append(PaletteCommand(
                    id="notetaker",
                    title=title,
                    description="Meeting notes & deliverables",
                    category="Meeting",
                    shortcut="",
                ))
            for v in db.search_vocabulary(query=query, active_only=False)[:3]:
                spoken = v.get("spoken_phrase") or ""
                rep = v.get("replacement") or ""
                matches.append(PaletteCommand(
                    id="dictionary",
                    title=f"{spoken} → {rep}",
                    description="Custom vocabulary entry",
                    category="Dictionary",
                    shortcut="",
                ))
            for s in db.list_snippets(search_query=query)[:3]:
                trigger = s.get("trigger_phrase") or ""
                matches.append(PaletteCommand(
                    id="snippets",
                    title=f'"{trigger}"',
                    description="Snippet trigger",
                    category="Snippets",
                    shortcut="",
                ))
            for n in db.list_notes(search_query=query)[:3]:
                title = n.get("title") or "Untitled note"
                matches.append(PaletteCommand(
                    id="scratchpad",
                    title=title,
                    description="Scratchpad note",
                    category="Scratchpad",
                    shortcut="",
                ))
        except Exception:
            pass
        return matches

    def _render_items(self) -> None:
        self.list_widget.clear()
        for cmd in self._filtered_commands:
            item = QListWidgetItem(self.list_widget)
            widget = CommandItemWidget(cmd, theme=self._theme, parent=self.list_widget)
            item.setSizeHint(widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        row = self.list_widget.row(item)
        if 0 <= row < len(self._filtered_commands):
            cmd = self._filtered_commands[row]
            self.accept()
            if cmd.handler:
                cmd.handler()
            self.command_executed.emit(cmd.id)

    def execute_selected(self) -> None:
        """Execute the currently selected item in the list."""
        current_row = self.list_widget.currentRow()
        if 0 <= current_row < len(self._filtered_commands):
            cmd = self._filtered_commands[current_row]
            self.accept()
            if cmd.handler:
                cmd.handler()
            self.command_executed.emit(cmd.id)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Down:
            curr = self.list_widget.currentRow()
            if curr < self.list_widget.count() - 1:
                self.list_widget.setCurrentRow(curr + 1)
            event.accept()
        elif key == Qt.Key.Key_Up:
            curr = self.list_widget.currentRow()
            if curr > 0:
                self.list_widget.setCurrentRow(curr - 1)
            event.accept()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.execute_selected()
            event.accept()
        elif key == Qt.Key.Key_Space:
            if self.txt_search.hasFocus():
                super().keyPressEvent(event)
            else:
                self.execute_selected()
                event.accept()
        elif key == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
        else:
            super().keyPressEvent(event)

    def show_palette(self) -> None:
        """Center relative to parent and present search focus."""
        if self.parent():
            p_geom = self.parent().geometry()
            x = p_geom.x() + (p_geom.width() - self.width()) // 2
            y = p_geom.y() + max(40, (p_geom.height() - self.height()) // 3)
            self.move(x, y)
        self.txt_search.clear()
        self._filter_commands("")
        self.show()
        self.activateWindow()
        self.txt_search.setFocus()


def create_default_vox_commands(main_window: Any, controller: Optional[Any] = None) -> List[PaletteCommand]:
    """Create default palette commands registering the 8 workspace routes and global actions."""
    cmds: List[PaletteCommand] = []
    ctrl = controller or getattr(main_window, "controller", None)

    # 1. 8 Primary Workspace Routes
    cmds.append(PaletteCommand(
        id="home",
        title="Home",
        description="Activity feed, dictation history, and quick status",
        category="Workspace",
        shortcut="Ctrl+1",
        handler=lambda: main_window.navigate_to_workspace("home") if hasattr(main_window, "navigate_to_workspace") else None,
    ))
    cmds.append(PaletteCommand(
        id="notetaker",
        title="Notetaker",
        description="Meeting intelligence, live transcripts, and deliverables",
        category="Workspace",
        shortcut="Ctrl+2",
        handler=lambda: main_window.navigate_to_workspace("notetaker") if hasattr(main_window, "navigate_to_workspace") else None,
    ))
    cmds.append(PaletteCommand(
        id="dictionary",
        title="Dictionary",
        description="Custom vocabulary, jargon, and phonetic replacements",
        category="Workspace",
        shortcut="Ctrl+3",
        handler=lambda: main_window.navigate_to_workspace("dictionary") if hasattr(main_window, "navigate_to_workspace") else None,
    ))
    cmds.append(PaletteCommand(
        id="snippets",
        title="Snippets",
        description="Keyword expansion triggers and canned responses",
        category="Workspace",
        shortcut="Ctrl+4",
        handler=lambda: main_window.navigate_to_workspace("snippets") if hasattr(main_window, "navigate_to_workspace") else None,
    ))
    cmds.append(PaletteCommand(
        id="style",
        title="Style",
        description="Personal tone preferences and transformation profiles",
        category="Workspace",
        shortcut="Ctrl+5",
        handler=lambda: main_window.navigate_to_workspace("style") if hasattr(main_window, "navigate_to_workspace") else None,
    ))
    cmds.append(PaletteCommand(
        id="scratchpad",
        title="Scratchpad",
        description="Document drafts, notes, and dictation staging",
        category="Workspace",
        shortcut="Ctrl+6",
        handler=lambda: main_window.navigate_to_workspace("scratchpad") if hasattr(main_window, "navigate_to_workspace") else None,
    ))
    cmds.append(PaletteCommand(
        id="insights",
        title="Insights",
        description="Usage statistics, speaking speed WPM, and time saved",
        category="Workspace",
        shortcut="Ctrl+7",
        handler=lambda: main_window.navigate_to_workspace("insights") if hasattr(main_window, "navigate_to_workspace") else None,
    ))
    cmds.append(PaletteCommand(
        id="calendar",
        title="Calendar",
        description="Upcoming meetings, Outlook/Google/Zoom accounts, and Notetaker prep",
        category="Workspace",
        shortcut="Ctrl+8",
        handler=lambda: main_window.navigate_to_workspace("calendar") if hasattr(main_window, "navigate_to_workspace") else None,
    ))
    cmds.append(PaletteCommand(
        id="settings",
        title="Settings",
        description="Audio devices, hotkeys, startup, and AI model preferences",
        category="Workspace",
        shortcut="Ctrl+,",
        handler=lambda: main_window.navigate_to_workspace("settings") if hasattr(main_window, "navigate_to_workspace") else None,
    ))

    # 2. Global Actions
    def _action_start_dictation() -> None:
        if ctrl and hasattr(ctrl, "start_dictation"):
            ctrl.start_dictation()

    def _action_toggle_theme() -> None:
        if hasattr(main_window, "toggle_theme"):
            main_window.toggle_theme()
        elif ctrl and hasattr(ctrl, "toggle_theme"):
            ctrl.toggle_theme()

    def _action_open_scratchpad() -> None:
        if hasattr(main_window, "open_floating_scratchpad"):
            main_window.open_floating_scratchpad()
        elif hasattr(main_window, "_emit_open_scratchpad"):
            main_window._emit_open_scratchpad()

    def _action_open_drawer() -> None:
        if hasattr(main_window, "open_notetaker_drawer"):
            main_window.open_notetaker_drawer()
        elif hasattr(main_window, "_emit_open_drawer"):
            main_window._emit_open_drawer()

    def _action_replay_tour() -> None:
        if hasattr(main_window, "replay_onboarding_tour"):
            main_window.replay_onboarding_tour()
        elif hasattr(main_window, "replay_tour_requested"):
            main_window.replay_tour_requested.emit()

    cmds.append(PaletteCommand(
        id="action_dictation",
        title="Start Dictation",
        description="Begin voice dictation input",
        category="Action",
        shortcut="Ctrl+Win",
        handler=_action_start_dictation,
    ))
    cmds.append(PaletteCommand(
        id="action_theme",
        title="Toggle Theme",
        description="Switch between Sand Light and Vast Dark themes",
        category="Action",
        shortcut="Ctrl+T",
        handler=_action_toggle_theme,
    ))
    cmds.append(PaletteCommand(
        id="action_scratchpad",
        title="Open Floating Scratchpad",
        description="Open resizable floating quick-note window",
        category="Action",
        shortcut="",
        handler=_action_open_scratchpad,
    ))
    cmds.append(PaletteCommand(
        id="action_drawer",
        title="Open Notetaker Drawer",
        description="Open companion notetaker drawer for call notes",
        category="Action",
        shortcut="",
        handler=_action_open_drawer,
    ))
    cmds.append(PaletteCommand(
        id="action_tour",
        title="Replay Onboarding Tour",
        description="Launch the 4-step interactive onboarding walkthrough",
        category="Action",
        shortcut="",
        handler=_action_replay_tour,
    ))

    return cmds

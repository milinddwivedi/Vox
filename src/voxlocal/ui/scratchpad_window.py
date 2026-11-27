"""VOX Floating Scratchpad Window — Quick Voice Notes & AI Transformation Canvas.

Matches Wispr Flow's Scratchpad window specification (720x760 default, 500x600 min),
backed by VOX's local SQLite `scratchpad_notes` & `scratchpad_versions` tables,
rich cursor formatting, live voice dictation insertion, and local AI rewrite presets.
"""

from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction, QTextCharFormat, QTextListFormat
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from voxlocal.core.controller import AppController
from voxlocal.ui.theme import get_dialog_stylesheet, get_theme_tokens, get_theme_stylesheet, get_ui_font


class VoxScratchpadWindow(QWidget):
    """Standalone resizable quick-capture Scratchpad persisted in SQLite."""

    def __init__(
        self,
        controller: AppController,
        on_open_hub_scratchpad: Optional[callable] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.on_open_hub_scratchpad = on_open_hub_scratchpad
        self._theme = controller.theme if controller else "light"
        self._current_note_id: Optional[int] = None
        self._dirty: bool = False

        self.setWindowTitle("VOX — Scratchpad")
        self.resize(720, 760)
        self.setMinimumSize(500, 600)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(650)
        self._autosave_timer.timeout.connect(self.save_current_note)

        self._build_ui()
        self.apply_theme(self._theme)
        self.load_initial_note()

        if self.controller:
            self.controller.theme_changed.connect(self.apply_theme)
            self.controller.dictation_completed.connect(self._on_dictation_ready)
            if hasattr(self.controller, "dictation_interim_updated"):
                self.controller.dictation_interim_updated.connect(self._on_dictation_interim_updated)
            if hasattr(self.controller, "dictation_state_changed"):
                self.controller.dictation_state_changed.connect(self._on_dictation_state)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        # Header Bar
        top = QHBoxLayout()
        top.setSpacing(8)

        self.combo_recent = QComboBox()
        self.combo_recent.setMinimumWidth(200)
        self.combo_recent.currentIndexChanged.connect(self._on_recent_selected)
        top.addWidget(self.combo_recent)

        self.btn_new = QPushButton("+ New note")
        self.btn_new.setObjectName("dawnBtn")
        self.btn_new.clicked.connect(self.create_fresh_note)
        top.addWidget(self.btn_new)

        self.btn_pin = QPushButton("Pin")
        self.btn_pin.setCheckable(True)
        self.btn_pin.clicked.connect(self._on_pin_toggled)
        top.addWidget(self.btn_pin)

        top.addStretch()

        self.btn_dictate = QPushButton("Dictate Here")
        self.btn_dictate.setObjectName("fathomBtn")
        self.btn_dictate.clicked.connect(self._toggle_dictation_into_note)
        top.addWidget(self.btn_dictate)

        self.btn_versions = QPushButton("History")
        self.btn_versions.clicked.connect(self._show_versions_dialog)
        top.addWidget(self.btn_versions)

        if self.on_open_hub_scratchpad:
            self.btn_hub = QPushButton("Open in Hub")
            self.btn_hub.clicked.connect(self.on_open_hub_scratchpad)
            top.addWidget(self.btn_hub)

        root.addLayout(top)

        # Note Title
        self.txt_title = QLineEdit()
        self.txt_title.setPlaceholderText("Note title...")
        self.txt_title.setFont(get_ui_font(18, 600, serif=True))
        self.txt_title.textEdited.connect(lambda: self._mark_dirty())
        root.addWidget(self.txt_title)

        # Formatting Toolbar
        fmt_bar = QFrame()
        fmt_bar.setObjectName("voxWellCard")
        fb_lay = QHBoxLayout(fmt_bar)
        fb_lay.setContentsMargins(8, 5, 8, 5)
        fb_lay.setSpacing(4)

        for label, tip, handler in [
            ("B", "Bold", lambda: self._toggle_weight()),
            ("I", "Italic", lambda: self.editor.setFontItalic(not self.editor.fontItalic())),
            ("U", "Underline", lambda: self.editor.setFontUnderline(not self.editor.fontUnderline())),
            ("• List", "Bulleted list", lambda: self._insert_list(QTextListFormat.Style.ListDisc)),
            ("1. List", "Numbered list", lambda: self._insert_list(QTextListFormat.Style.ListDecimal)),
            ("[ ] Task", "Insert checklist item", lambda: self.editor.insertPlainText("☐ ")),
            ("</> Code", "Inline code font", lambda: self._toggle_mono()),
        ]:
            b = QPushButton(label)
            b.setObjectName("ghostBtn")
            b.setToolTip(tip)
            b.clicked.connect(handler)
            fb_lay.addWidget(b)

        fb_lay.addStretch()
        self.lbl_save_status = QLabel("Saved locally")
        self.lbl_save_status.setObjectName("mutedLabel")
        fb_lay.addWidget(self.lbl_save_status)
        root.addWidget(fmt_bar)

        # Main Rich Text Editor
        self.editor = QTextEdit()
        self.editor.setPlaceholderText(
            "Speak or type freely... capture quick thoughts, structured outlines, drafts, or follow-ups."
        )
        self.editor.setFont(get_ui_font(14, 400))
        self.editor.textChanged.connect(self._mark_dirty)
        root.addWidget(self.editor, 1)

        self.lbl_live_interim = QLabel("")
        self.lbl_live_interim.setObjectName("lblScratchpadInterim")
        self.lbl_live_interim.setWordWrap(True)
        self.lbl_live_interim.setStyleSheet("color: #8E8D8A; font-style: italic; font-size: 13px; padding: 4px 10px; background: rgba(0,0,0,0.03); border-radius: 6px;")
        self.lbl_live_interim.setVisible(False)
        root.addWidget(self.lbl_live_interim)

        # AI Transform / Rewrite Bar
        ai_card = QFrame()
        ai_card.setObjectName("voxCard")
        ai_lay = QVBoxLayout(ai_card)
        ai_lay.setContentsMargins(12, 10, 12, 10)
        ai_lay.setSpacing(8)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        lbl_ai = QLabel("Transform note:")
        lbl_ai.setFont(get_ui_font(12, 600))
        preset_row.addWidget(lbl_ai)
        for lbl, cmd in [
            ("Polish", "polish"),
            ("Concise", "concise"),
            ("Formal", "formal"),
            ("Bullet list", "bullet_list"),
            ("Email draft", "email_draft"),
        ]:
            pb = QPushButton(lbl)
            pb.clicked.connect(lambda _c=False, c=cmd: self._apply_transform(c))
            preset_row.addWidget(pb)
        preset_row.addStretch()
        ai_lay.addLayout(preset_row)

        ask_row = QHBoxLayout()
        ask_row.setSpacing(8)
        self.txt_instruction = QLineEdit()
        self.txt_instruction.setPlaceholderText("Follow up or instruct rewrite (e.g. 'make it shorter and clearer')...")
        self.txt_instruction.returnPressed.connect(
            lambda: self._apply_transform(self.txt_instruction.text())
        )
        ask_row.addWidget(self.txt_instruction, 1)

        self.btn_apply_ai = QPushButton("Transform")
        self.btn_apply_ai.setObjectName("primaryBtn")
        self.btn_apply_ai.clicked.connect(
            lambda: self._apply_transform(self.txt_instruction.text() or "polish")
        )
        ask_row.addWidget(self.btn_apply_ai)
        ai_lay.addLayout(ask_row)
        root.addWidget(ai_card)

        # Bottom Actions
        bottom = QHBoxLayout()
        self.btn_copy = QPushButton("Copy note")
        self.btn_copy.clicked.connect(self._copy_note)
        bottom.addWidget(self.btn_copy)

        self.btn_delete = QPushButton("Delete note")
        self.btn_delete.setObjectName("dangerBtn")
        self.btn_delete.clicked.connect(self._delete_current_note)
        bottom.addWidget(self.btn_delete)
        bottom.addStretch()
        self.btn_save = QPushButton("Save")
        self.btn_save.setObjectName("primaryBtn")
        self.btn_save.clicked.connect(self.save_current_note)
        bottom.addWidget(self.btn_save)
        root.addLayout(bottom)

    def apply_theme(self, theme: str) -> None:
        self._theme = theme or "light"
        self.setStyleSheet(get_theme_stylesheet(self._theme))

    def _mark_dirty(self) -> None:
        self._dirty = True
        self.lbl_save_status.setText("Editing...")
        self._autosave_timer.start()

    def _toggle_weight(self) -> None:
        fmt = self.editor.currentCharFormat()
        is_bold = fmt.fontWeight() > 500
        self.editor.setFontWeight(400 if is_bold else 700)

    def _toggle_mono(self) -> None:
        fmt = QTextCharFormat()
        curr_fam = self.editor.currentFont().family().lower()
        from voxlocal.ui.theme import get_mono_font_family, get_primary_font_family
        fmt.setFontFamily(get_primary_font_family() if ("consolas" in curr_fam or "mono" in curr_fam or "code" in curr_fam) else get_mono_font_family())
        self.editor.mergeCurrentCharFormat(fmt)

    def _insert_list(self, style: QTextListFormat.Style) -> None:
        cursor = self.editor.textCursor()
        cursor.createList(style)

    def refresh_recent_combo(self, select_id: Optional[int] = None) -> None:
        notes = self.controller.db.list_notes()
        self.combo_recent.blockSignals(True)
        self.combo_recent.clear()
        sel_idx = 0
        for idx, n in enumerate(notes):
            prefix = "★ " if n.get("is_pinned") else ""
            self.combo_recent.addItem(f"{prefix}{n.get('title', 'Untitled')}", int(n["id"]))
            if select_id is not None and int(n["id"]) == int(select_id):
                sel_idx = idx
        if notes:
            self.combo_recent.setCurrentIndex(sel_idx)
        self.combo_recent.blockSignals(False)

    def load_initial_note(self) -> None:
        behavior = getattr(self.controller.config, "scratchpad_open_behavior", "last_note")
        notes = self.controller.db.list_notes()
        if behavior == "new_note" or not notes:
            self.create_fresh_note()
        else:
            self._load_note_dict(notes[0])
            self.refresh_recent_combo(notes[0]["id"])

    def _load_note_dict(self, note: dict) -> None:
        self._current_note_id = int(note["id"])
        self.txt_title.blockSignals(True)
        self.editor.blockSignals(True)
        self.txt_title.setText(str(note.get("title", "Untitled note")))
        content = str(note.get("content", "") or "")
        if content.lstrip().startswith("<") and "</" in content:
            self.editor.setHtml(content)
        else:
            self.editor.setPlainText(str(note.get("plain_text") or content))
        self.btn_pin.blockSignals(True)
        self.btn_pin.setChecked(bool(note.get("is_pinned", 0)))
        self.btn_pin.blockSignals(False)
        self.txt_title.blockSignals(False)
        self.editor.blockSignals(False)
        self._dirty = False
        self.lbl_save_status.setText("Saved locally")

    def _on_recent_selected(self, idx: int) -> None:
        if idx < 0:
            return
        self.save_current_note()
        nid = self.combo_recent.itemData(idx)
        if nid is not None:
            note = self.controller.db.get_note(int(nid))
            if note:
                self._load_note_dict(note)

    def create_fresh_note(self) -> None:
        if self._current_note_id is not None and self._dirty:
            self.save_current_note()
        nid = self.controller.db.create_note(title="Untitled note", content="", plain_text="")
        note = self.controller.db.get_note(nid)
        if note:
            self._load_note_dict(note)
        self.refresh_recent_combo(nid)
        self.controller.notes_updated.emit()

    def save_current_note(self) -> None:
        title = self.txt_title.text().strip() or "Untitled note"
        html = self.editor.toHtml()
        plain = self.editor.toPlainText().strip()
        if self._current_note_id is None:
            self._current_note_id = self.controller.db.create_note(
                title=title, content=html, plain_text=plain, is_pinned=int(self.btn_pin.isChecked())
            )
        else:
            self.controller.db.update_note(
                self._current_note_id,
                title=title,
                content=html,
                plain_text=plain,
                is_pinned=int(self.btn_pin.isChecked()),
            )
        self._dirty = False
        self.lbl_save_status.setText("Saved")
        self.refresh_recent_combo(self._current_note_id)
        self.controller.notes_updated.emit()

    def _on_pin_toggled(self, checked: bool) -> None:
        if self._current_note_id is not None:
            self.controller.db.update_note(self._current_note_id, is_pinned=int(checked), save_version=False)
            self.refresh_recent_combo(self._current_note_id)
            self.controller.notes_updated.emit()

    def _toggle_dictation_into_note(self) -> None:
        self.editor.setFocus()
        if hasattr(self.controller, "toggle_in_app_dictation"):
            self.controller.toggle_in_app_dictation(self.editor, "VOX — Scratchpad")
        else:
            self.controller.toggle_dictation()

    def _on_dictation_interim_updated(self, provisional: str, stable: str) -> None:
        app = QApplication.instance()
        active_win = app.activeWindow() if app else None
        is_target = (active_win is self) or self.isActiveWindow() or self.editor.hasFocus()
        if not is_target:
            if hasattr(self, "lbl_live_interim"):
                self.lbl_live_interim.setText("")
                self.lbl_live_interim.setVisible(False)
            return

        combined = " ".join(x for x in (stable.strip(), provisional.strip()) if x).strip()
        if combined and hasattr(self, "lbl_live_interim"):
            self.lbl_live_interim.setText(f"🎙️ Live: {combined}")
            self.lbl_live_interim.setVisible(True)
        elif hasattr(self, "lbl_live_interim"):
            self.lbl_live_interim.setText("")
            self.lbl_live_interim.setVisible(False)

    def _on_dictation_state(self, state: str, _msg: str = "") -> None:
        if state in ("listening", "speaking") and (
            self.isActiveWindow() or self.editor.hasFocus()
        ):
            self.btn_dictate.setText("⏹ Stop Dictating")
        elif state in ("processing", "stopping"):
            self.btn_dictate.setText("Transcribing...")
        else:
            self.btn_dictate.setText("Dictate Here")
            if hasattr(self, "lbl_live_interim"):
                self.lbl_live_interim.setText("")
                self.lbl_live_interim.setVisible(False)

    def _on_dictation_ready(self, final_text: str, _target: str = "", method_used: str = "") -> None:
        if hasattr(self, "lbl_live_interim"):
            self.lbl_live_interim.setText("")
            self.lbl_live_interim.setVisible(False)
        if not final_text or not final_text.strip():
            return

        app = QApplication.instance()
        active_win = app.activeWindow() if app else None
        is_target = (
            (active_win is self)
            or self.isActiveWindow()
            or self.editor.hasFocus()
            or (method_used == "in_app_qt" and self.isVisible())
            or ("scratchpad" in _target.lower() if _target else False)
        )
        if not is_target:
            return

        # Check if already inserted directly into editor
        current_text = self.editor.toPlainText()
        tail = current_text[-max(40, len(final_text) + 10) :] if current_text else ""
        if final_text.strip() not in tail:
            cursor = self.editor.textCursor()
            existing = self.editor.toPlainText()
            pos = cursor.position()
            if final_text == "\n":
                to_insert = "\n"
            elif existing and pos > 0 and not existing[pos - 1].isspace() and not final_text.startswith(" "):
                to_insert = " " + final_text.lstrip()
            else:
                to_insert = final_text
            cursor.insertText(to_insert)
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()

        self._autosave_timer.start()

    def hideEvent(self, event) -> None:
        if self.controller and hasattr(self.controller, "clear_in_app_dictation_target"):
            self.controller.clear_in_app_dictation_target(self.editor)
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        if self.controller and hasattr(self.controller, "clear_in_app_dictation_target"):
            self.controller.clear_in_app_dictation_target(self.editor)
        super().closeEvent(event)

    def _apply_transform(self, instruction: str) -> None:
        plain = self.editor.toPlainText().strip()
        if not plain:
            return
        self.save_current_note()
        transformed = self.controller.transform_text(plain, instruction=instruction)
        if transformed:
            self.editor.setPlainText(transformed)
            self.save_current_note()
            self.txt_instruction.clear()

    def _copy_note(self) -> None:
        plain = self.editor.toPlainText().strip()
        if plain:
            cb = QApplication.clipboard()
            if cb:
                cb.setText(plain)
            self.lbl_save_status.setText("Copied to clipboard")

    def _delete_current_note(self) -> None:
        if self._current_note_id is not None:
            self.controller.db.delete_note(self._current_note_id)
            self._current_note_id = None
            self.controller.notes_updated.emit()
        self.load_initial_note()

    def _show_versions_dialog(self) -> None:
        if self._current_note_id is None:
            return
        versions = self.controller.db.list_note_versions(self._current_note_id, limit=15)
        dlg = QDialog(self)
        dlg.setWindowTitle("Scratchpad Version History")
        dlg.resize(440, 360)
        dlg.setStyleSheet(get_dialog_stylesheet(self._theme))
        lay = QVBoxLayout(dlg)
        lst = QListWidget()
        for v in versions:
            item = QListWidgetItem(f"{v.get('created_at', '')}")
            item.setData(Qt.ItemDataRole.UserRole, v.get("content", ""))
            lst.addItem(item)
        lay.addWidget(lst, 1)
        btn_restore = QPushButton("Restore Selected Version")
        btn_restore.setObjectName("primaryBtn")

        def _restore() -> None:
            cur = lst.currentItem()
            if cur:
                c = cur.data(Qt.ItemDataRole.UserRole) or ""
                if str(c).lstrip().startswith("<"):
                    self.editor.setHtml(str(c))
                else:
                    self.editor.setPlainText(str(c))
                self.save_current_note()
            dlg.accept()

        btn_restore.clicked.connect(_restore)
        lay.addWidget(btn_restore)
        dlg.exec()

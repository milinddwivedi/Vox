"""System tray icon and context menu for VOX."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from voxlocal.ui.theme import create_app_icon, get_theme_tokens

if TYPE_CHECKING:
    from voxlocal.core.controller import AppController
    from voxlocal.ui.floating_widget import FloatingWidget


class SystemTrayManager:
    """Manages the Windows system tray icon, VOX status badge, and quick actions."""

    def __init__(
        self,
        parent_widget: QWidget,
        controller: AppController,
        floating_widget: FloatingWidget,
        on_open_tab: Callable[[int], None],
        on_exit_app: Callable[[], None],
        on_open_scratchpad: Optional[Callable[[], None]] = None,
        on_open_notetaker: Optional[Callable[[], None]] = None,
    ) -> None:
        self.parent_widget = parent_widget
        self.controller = controller
        self.floating_widget = floating_widget
        self.on_open_tab = on_open_tab
        self.on_exit_app = on_exit_app
        self.on_open_scratchpad = on_open_scratchpad
        self.on_open_notetaker = on_open_notetaker

        self.tray = QSystemTrayIcon(create_app_icon(size=64, theme="light"), parent_widget)
        self.tray.setToolTip("VOX — Voice Dictation & Notetaker")
        self.menu = QMenu(parent_widget)

        # Status header (read-only indicator)
        self.act_status = QAction("● Ready", self.menu)
        self.act_status.setEnabled(False)
        self.menu.addAction(self.act_status)

        # Plan status & Upgrade action (Milestone 2)
        self.act_plan = QAction("Plan: VOX Free", self.menu)
        self.act_plan.setEnabled(False)
        self.menu.addAction(self.act_plan)

        self.act_upgrade = QAction("⚡ Upgrade to Pro...", self.menu)
        self.act_upgrade.triggered.connect(
            lambda: self.controller.request_upgrade_dialog("Upgrade to VOX Pro")
            if hasattr(self.controller, "request_upgrade_dialog")
            else None
        )
        self.menu.addAction(self.act_upgrade)
        self.menu.addSeparator()

        # Primary window & floating surfaces
        self.act_open = QAction("Open VOX Hub", self.menu)
        self.act_open.triggered.connect(lambda: self.on_open_tab(0))
        self.menu.addAction(self.act_open)

        self.act_scratchpad = QAction("Open Scratchpad", self.menu)
        self.act_scratchpad.triggered.connect(self._open_scratchpad)
        self.menu.addAction(self.act_scratchpad)

        self.act_notetaker_drawer = QAction("Open Notetaker Drawer", self.menu)
        self.act_notetaker_drawer.triggered.connect(self._open_notetaker_drawer)
        self.menu.addAction(self.act_notetaker_drawer)

        self.act_widget = QAction("Toggle Flow Bar", self.menu)
        self.act_widget.triggered.connect(self._toggle_floating_widget)
        self.menu.addAction(self.act_widget)
        self.menu.addSeparator()

        # Capture actions
        self.act_dictation = QAction("Start Dictation", self.menu)
        self.act_dictation.triggered.connect(self.controller.toggle_dictation)
        self.menu.addAction(self.act_dictation)

        self.act_meeting_start = QAction("Start Notetaker (Call Capture)", self.menu)
        self.act_meeting_start.triggered.connect(self.controller.toggle_meeting_recording)
        self.menu.addAction(self.act_meeting_start)

        self.act_meeting_pause = QAction("Pause Notetaker", self.menu)
        self.act_meeting_pause.triggered.connect(self._toggle_meeting_pause)
        self.act_meeting_pause.setVisible(False)
        self.menu.addAction(self.act_meeting_pause)

        self.act_screen_start = QAction("Record Screen", self.menu)
        self.act_screen_start.triggered.connect(self._toggle_screen_recording)
        self.menu.addAction(self.act_screen_start)
        self.menu.addSeparator()

        # Hub Workspace jumps (preserving legacy attribute names and index map for compatibility)
        self.act_tab_voice = QAction("Home", self.menu)
        self.act_tab_voice.triggered.connect(lambda: self.on_open_tab(0))
        self.menu.addAction(self.act_tab_voice)

        self.act_tab_meeting = QAction("Notetaker & Call Notes", self.menu)
        self.act_tab_meeting.triggered.connect(lambda: self.on_open_tab(2))
        self.menu.addAction(self.act_tab_meeting)
        self.act_tab_studio = self.act_tab_meeting

        self.act_vocab = QAction("Dictionary", self.menu)
        self.act_vocab.triggered.connect(lambda: self.on_open_tab(6))
        self.menu.addAction(self.act_vocab)

        self.act_tab_snippets = QAction("Snippets", self.menu)
        self.act_tab_snippets.triggered.connect(lambda: self.on_open_tab(8))
        self.menu.addAction(self.act_tab_snippets)

        self.act_tab_style = QAction("Style Preferences", self.menu)
        self.act_tab_style.triggered.connect(lambda: self.on_open_tab(5))
        self.menu.addAction(self.act_tab_style)

        self.act_tab_scratchpad = QAction("Scratchpad Notes", self.menu)
        self.act_tab_scratchpad.triggered.connect(lambda: self.on_open_tab(4))
        self.menu.addAction(self.act_tab_scratchpad)

        self.act_history = QAction("Insights", self.menu)
        self.act_history.triggered.connect(lambda: self.on_open_tab(3))
        self.menu.addAction(self.act_history)

        self.act_tab_screen = QAction("Screen Capture", self.menu)
        self.act_tab_screen.triggered.connect(lambda: self.on_open_tab(1))
        self.act_tab_screen.setVisible(False)
        self.menu.addAction(self.act_tab_screen)

        self.act_settings = QAction("Settings...", self.menu)
        self.act_settings.triggered.connect(lambda: self.on_open_tab(7))
        self.menu.addAction(self.act_settings)

        self.menu.addSeparator()
        self.act_exit = QAction("Exit VOX", self.menu)
        self.act_exit.triggered.connect(self.on_exit_app)
        self.menu.addAction(self.act_exit)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        self.controller.dictation_state_changed.connect(lambda *_: self._refresh_state())
        self.controller.meeting_state_changed.connect(lambda *_: self._refresh_state())
        self.controller.screen_recording_state_changed.connect(lambda *_: self._refresh_state())
        self.controller.theme_changed.connect(self.apply_theme)
        if hasattr(self.controller, "entitlement_changed"):
            self.controller.entitlement_changed.connect(lambda *_: self._refresh_state())
        if hasattr(self.controller, "usage_updated"):
            self.controller.usage_updated.connect(lambda *_: self._refresh_state())
        if hasattr(self.controller, "auth_state_changed"):
            self.controller.auth_state_changed.connect(lambda *_: self._refresh_state())
        self.apply_theme(getattr(self.controller, "theme", getattr(self.controller.config, "theme", "light")))
        self._refresh_state()

    def _open_scratchpad(self) -> None:
        if callable(self.on_open_scratchpad):
            self.on_open_scratchpad()
        elif hasattr(self.controller, "open_scratchpad_requested"):
            self.controller.open_scratchpad_requested.emit()
        else:
            self.on_open_tab(4)

    def _open_notetaker_drawer(self) -> None:
        if callable(self.on_open_notetaker):
            self.on_open_notetaker()
        elif hasattr(self.controller, "open_notetaker_drawer_requested"):
            self.controller.open_notetaker_drawer_requested.emit()
        else:
            self.on_open_tab(2)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger,
        ):
            self.on_open_tab(0)

    def _toggle_floating_widget(self) -> None:
        if self.floating_widget is None:
            return
        if self.floating_widget.isVisible():
            self.floating_widget.hide()
        else:
            self.floating_widget.show()
            self.floating_widget.raise_()
        if self.controller and getattr(self.controller, "config", None):
            self.controller.config.show_floating_widget = self.floating_widget.isVisible()
            self.controller.config.save()

    def _toggle_screen_recording(self) -> None:
        if bool(getattr(self.controller, "is_screen_recording_active", False)):
            self.controller.stop_screen_recording()
        else:
            self.controller.start_screen_recording()

    def _toggle_meeting_pause(self) -> None:
        if getattr(self.controller, "meeting_state", "idle") == "paused":
            self.controller.resume_meeting_recording()
        else:
            self.controller.pause_meeting_recording()

    def apply_theme(self, theme: str = "light") -> None:
        resolved = "dark" if (theme or "").lower() == "dark" else "light"
        t = get_theme_tokens(resolved)
        self.tray.setIcon(create_app_icon(size=64, theme=resolved))
        sel_bg = t["accent_dawn"] if resolved == "light" else t["sidebar_active"]
        sel_text = "#1A1A1A" if resolved == "light" else t["text"]
        self.menu.setStyleSheet(
            f"""
            QMenu {{
                background-color: {t['surface']};
                color: {t['text']};
                border: 1px solid {t['border']};
                border-radius: 10px;
                padding: 6px;
                font-size: 12.5px;
            }}
            QMenu::item {{
                padding: 6px 18px 6px 12px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background-color: {sel_bg};
                color: {sel_text};
            }}
            QMenu::item:disabled {{
                color: {t['text_muted']};
                font-weight: 600;
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {t['line']};
                margin: 4px 8px;
            }}
            """
        )

    def _refresh_state(self) -> None:
        active_labels = []
        d_state = getattr(self.controller, "dictation_state", "idle")
        m_state = getattr(self.controller, "meeting_state", "idle")

        if bool(getattr(self.controller, "is_screen_recording_active", False)):
            self.act_screen_start.setText("Stop Screen Recording")
            active_labels.append("Screen")
        else:
            self.act_screen_start.setText("Record Screen")

        if d_state in ("listening", "hands_free"):
            self.act_dictation.setText("Stop Dictation")
            active_labels.append("Dictating")
        elif d_state == "transcribing":
            self.act_dictation.setText("Polishing...")
            active_labels.append("Polishing")
        else:
            ptt = getattr(self.controller.config, "ptt_hotkey", "ctrl+win")
            self.act_dictation.setText(f"Start Dictation ({ptt})")

        if bool(getattr(self.controller, "is_meeting_recording", False)):
            self.act_meeting_start.setText("Stop & Save Notetaker")
            self.act_meeting_pause.setVisible(True)
            self.act_meeting_pause.setText("Pause Notetaker")
            started_at = getattr(self.controller, "_meeting_started_at", 0.0)
            elapsed = max(0, int(time.monotonic() - started_at)) if started_at else 0
            active_labels.append(f"Notetaker {elapsed // 60:02d}:{elapsed % 60:02d}")
        elif m_state == "paused":
            self.act_meeting_start.setText("Stop & Save Notetaker")
            self.act_meeting_pause.setVisible(True)
            self.act_meeting_pause.setText("Resume Notetaker")
            active_labels.append("Notetaker Paused")
        else:
            hotkey = getattr(self.controller.config, "meeting_hotkey", "ctrl+alt+r")
            self.act_meeting_start.setText(f"Start Notetaker ({hotkey})")
            self.act_meeting_pause.setVisible(False)

        if d_state == "failed" or m_state == "error":
            summary_str = "Attention needed"
        elif active_labels:
            summary_str = " · ".join(active_labels)
        else:
            summary_str = "Ready"

        self.act_status.setText(f"● {summary_str}")

        # Refresh plan badge & upgrade text
        ent = {}
        if hasattr(self.controller, "entitlement") and hasattr(self.controller.entitlement, "get_effective_entitlement"):
            try:
                ent = self.controller.entitlement.get_effective_entitlement()
            except Exception:
                pass
        is_pro = bool(ent.get("is_pro", False))
        if is_pro:
            self.act_plan.setText("Plan: VOX Pro")
            self.act_upgrade.setText("⚡ Manage Pro Plan...")
        else:
            self.act_plan.setText("Plan: VOX Free")
            self.act_upgrade.setText("⚡ Upgrade to Pro...")

    def show_notification(self, title: str, message: str) -> None:
        if self.tray.isVisible() and self.tray.supportsMessages():
            self.tray.showMessage(
                title,
                message,
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )

    def hide(self) -> None:
        self.tray.hide()

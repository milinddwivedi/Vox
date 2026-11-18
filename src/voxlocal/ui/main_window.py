"""VOX Primary Desktop Hub — Wispr Flow-Inspired Editorial Workspace."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from voxlocal import __version__
from voxlocal.core.logger import get_logger
from voxlocal.storage.exporter import format_seconds_hms
from voxlocal.ui.animations import ThemeToggleCapsule, ToastNotification
from voxlocal.ui.calendar_page import CalendarPage
from voxlocal.ui.command_palette import VoxCommandPaletteDialog, create_default_vox_commands
from voxlocal.ui.hub_pages import (
    DictionaryPage,
    HomePage,
    InsightsPage,
    ScratchpadHubPage,
    SnippetsPage,
    StylePage,
)
from voxlocal.ui.notetaker_page import NotetakerPage
from voxlocal.ui.settings_dialog import SettingsWorkspaceWidget, ShortcutCaptureDialog
from voxlocal.ui.theme import (
    create_app_icon,
    create_app_pixmap,
    get_theme_tokens,
    get_ui_font,
)

if TYPE_CHECKING:
    from voxlocal.core.controller import AppController
    from voxlocal.ui.floating_widget import FloatingWidget

logger = get_logger("ui.main_window")


def clean_summary_for_display(text: str) -> str:
    """Format meeting summary cleanly without markdown '#' or '*' headers."""
    if not text:
        return ""
    cleaned_lines = []
    for line in text.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("#"):
            hdr_text = trimmed.lstrip("#").strip()
            if hdr_text:
                cleaned_lines.append(hdr_text.upper())
                cleaned_lines.append("-" * min(60, max(24, len(hdr_text))))
        elif trimmed.startswith("*"):
            if trimmed.startswith("**") and trimmed.endswith("**"):
                cleaned_lines.append(trimmed.strip("* ").strip())
            else:
                body = trimmed.lstrip("* ").strip()
                cleaned_lines.append(f"• {body}" if body else "")
        else:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


class VoxWorkspaceStack(QStackedWidget):
    """Workspace stack exposing legacy QTabWidget helper aliases where tests expect them."""

    def __init__(self, labels: List[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._labels = list(labels)

    def tabText(self, index: int) -> str:
        if 0 <= index < len(self._labels):
            return self._labels[index]
        return ""


class GlobalSearchDialog(QDialog):
    """Ctrl+K universal jump palette across dictations, notes, meetings, snippets & words."""

    jump_requested = pyqtSignal(str, object)

    def __init__(self, controller: AppController, theme: str = "light", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._theme = "dark" if (theme or "").lower() == "dark" else "light"
        t = get_theme_tokens(self._theme)
        self.setWindowTitle("Search VOX")
        self.resize(560, 400)
        self.setStyleSheet(
            f"QDialog {{ background-color: {t['surface']}; color: {t['text']}; border: 1px solid {t['border']}; }}"
            f"QLineEdit {{ background: {t['input_bg']}; color: {t['text']}; border: 1px solid {t['border']}; "
            f"border-radius: 8px; padding: 8px 12px; font-size: 13px; }}"
            f"QListWidget {{ background: {t['bg']}; color: {t['text']}; border: 1px solid {t['line']}; border-radius: 8px; }}"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        self.txt = QLineEdit()
        self.txt.setAccessibleName("Search query input")
        self.txt.setAccessibleDescription("Enter search terms across dictations, meetings, dictionary, snippets, and scratchpad notes")
        self.txt.setPlaceholderText("Search dictations, meetings, dictionary, snippets, or notes...")
        self.txt.textChanged.connect(self._run_search)
        lay.addWidget(self.txt)

        self.results = QListWidget()
        self.results.setAccessibleName("Search results list")
        self.results.setAccessibleDescription("Matching results list across workspaces. Use arrow keys to select and Enter to open.")
        self.results.itemActivated.connect(self._activate)
        self.results.itemDoubleClicked.connect(self._activate)
        lay.addWidget(self.results, 1)

        self.setTabOrder(self.txt, self.results)

        self._run_search("")

    def _run_search(self, query: str) -> None:
        q = query.strip()
        self.results.clear()
        db = self.controller.db

        for d in db.list_dictation_history(search_query=q, limit=5):
            preview = str(d.get("final_text") or d.get("raw_text") or "").replace("\n", " ")[:70]
            it = QListWidgetItem(f"[Home · Dictation]  {preview}")
            it.setData(Qt.ItemDataRole.UserRole, ("home", d["id"]))
            self.results.addItem(it)

        for m in db.list_meetings(search_query=q, limit=5):
            it = QListWidgetItem(f"[Notetaker · Meeting]  {m.get('title') or 'Untitled Meeting'}")
            it.setData(Qt.ItemDataRole.UserRole, ("notetaker", m["id"]))
            self.results.addItem(it)

        for v in db.search_vocabulary(query=q, active_only=False)[:5]:
            it = QListWidgetItem(f"[Dictionary]  {v.get('spoken_phrase')} → {v.get('replacement')}")
            it.setData(Qt.ItemDataRole.UserRole, ("dictionary", v["id"]))
            self.results.addItem(it)

        for s in db.list_snippets(search_query=q)[:4]:
            it = QListWidgetItem(f"[Snippet]  \"{s.get('trigger_phrase')}\"")
            it.setData(Qt.ItemDataRole.UserRole, ("snippets", s["id"]))
            self.results.addItem(it)

        for n in db.list_notes(search_query=q)[:4]:
            it = QListWidgetItem(f"[Scratchpad]  {n.get('title') or 'Untitled note'}")
            it.setData(Qt.ItemDataRole.UserRole, ("scratchpad", n["id"]))
            self.results.addItem(it)

    def _activate(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            self.jump_requested.emit(str(data[0]), data[1])
            self.accept()


class MainWindow(QMainWindow):
    """Primary VOX Hub desktop application window matching Wispr Flow UX."""

    replay_tour_requested = pyqtSignal()

    WORKSPACES: List[Dict[str, str]] = [
        {"id": "home", "route": "/home", "shortcut": "Ctrl+1", "name": "Home"},
        {"id": "notetaker", "route": "/notetaker", "shortcut": "Ctrl+2", "name": "Notetaker"},
        {"id": "dictionary", "route": "/dictionary", "shortcut": "Ctrl+3", "name": "Dictionary"},
        {"id": "snippets", "route": "/snippets", "shortcut": "Ctrl+4", "name": "Snippets"},
        {"id": "style", "route": "/style", "shortcut": "Ctrl+5", "name": "Style"},
        {"id": "scratchpad", "route": "/scratchpad", "shortcut": "Ctrl+6", "name": "Scratchpad"},
        {"id": "insights", "route": "/insights", "shortcut": "Ctrl+7", "name": "Insights"},
        {"id": "settings", "route": "/settings", "shortcut": "Ctrl+,", "name": "Settings"},
    ]

    _ROUTE_TO_INDEX: Dict[str, int] = {
        "home": 0,
        "dictation": 0,
        "notetaker": 1,
        "meetings": 1,
        "dictionary": 2,
        "vocabulary": 2,
        "snippets": 3,
        "style": 4,
        "scratchpad": 5,
        "insights": 6,
        "history": 6,
        "analytics": 6,
        "settings": 7,
        "calendar": 8,
    }

    _LEGACY_INT_TO_INDEX: Dict[int, int] = {
        0: 0,  # Voice Typing -> Home
        1: 1,  # Screen / Notetaker -> Notetaker
        2: 1,  # Meeting Studio -> Notetaker
        3: 6,  # Reports / Insights -> Insights
        4: 5,  # Scratchpad
        5: 4,  # Style
        6: 2,  # Vocabulary -> Dictionary
        7: 7,  # Settings
        8: 3,  # Snippets
    }

    def __init__(
        self,
        controller: AppController,
        floating_widget: Optional[FloatingWidget] = None,
        on_exit_app: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.floating_widget = floating_widget
        self.on_exit_app = on_exit_app or (lambda: None)
        self._force_quit = False
        self._theme = getattr(controller, "theme", "light")
        self.nav_buttons: List[QPushButton] = []
        self.sidebar_buttons: List[QPushButton] = self.nav_buttons
        self._toasts: List[ToastNotification] = []

        # Ask VOX stream test/compatibility state
        self._current_ask_vox_id = 0
        self._ask_vox_is_streaming = False
        self._ask_vox_buffer = ""
        self.citations_list = QListWidget()
        self.citations_list.setAccessibleName("Ask VOX citation sources")
        self.citations_list.setAccessibleDescription("List of transcript citations and timestamps supporting the generated response")

        self.setWindowTitle("VOX")
        self.setWindowIcon(create_app_icon(size=64, theme=self._theme))
        self.resize(1350, 850)
        self.setMinimumSize(980, 640)

        self._build_ui()
        self._setup_shortcuts()
        self.apply_theme(self._theme)

        self.controller.theme_changed.connect(self.apply_theme)
        self.controller.dictation_state_changed.connect(lambda *_: self._update_float_btn_text())
        self.controller.meeting_state_changed.connect(lambda *_: self._update_float_btn_text())
        self.controller.notification_requested.connect(self.show_toast)
        if hasattr(self.controller, "upgrade_dialog_requested"):
            self.controller.upgrade_dialog_requested.connect(self._show_pro_upgrade_dialog)
        if hasattr(self.controller, "entitlement_changed"):
            self.controller.entitlement_changed.connect(lambda ent: self._update_plan_card(entitlement_data=ent))
        if hasattr(self.controller, "usage_updated"):
            self.controller.usage_updated.connect(lambda usg: self._update_plan_card(usage_data=usg))
        if hasattr(self.controller, "auth_state_changed"):
            self.controller.auth_state_changed.connect(lambda *_: self._update_plan_card())
        if hasattr(self.controller, "login_completed_signal"):
            self.controller.login_completed_signal.connect(self._on_login_completed)
        if hasattr(self.controller, "toast_requested"):
            self.controller.toast_requested.connect(self.show_toast)
        if hasattr(self.controller, "update_check_completed"):
            self.controller.update_check_completed.connect(self._on_update_available)
        if hasattr(self.controller, "model_status_changed"):
            self.controller.model_status_changed.connect(self._on_model_status_changed)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Left Sidebar (Warm Cream / Obsidian Dark) ----
        self.sidebar = QFrame()
        self.sidebar.setObjectName("leftSidebar")
        self.sidebar.setFixedWidth(228)
        sb_lay = QVBoxLayout(self.sidebar)
        sb_lay.setContentsMargins(14, 18, 14, 16)
        sb_lay.setSpacing(4)

        # Brand Row
        brand_row = QHBoxLayout()
        brand_row.setSpacing(8)
        self.lbl_logo_mark = QLabel()
        self.lbl_logo_mark.setFixedSize(28, 28)
        self.lbl_logo_mark.setAccessibleName("VOX application logo")
        self.lbl_logo_mark.setAccessibleDescription("Branded logo mark for VOX desktop assistant")
        self.lbl_brand = QLabel("VOX")
        self.lbl_brand.setFont(get_ui_font(20, 600, serif=True))
        self.lbl_brand.setAccessibleName("VOX Desktop Hub")
        self.lbl_brand.setAccessibleDescription("Primary application title")
        brand_row.addWidget(self.lbl_logo_mark)
        brand_row.addWidget(self.lbl_brand)
        brand_row.addStretch(1)

        self.theme_toggle = ThemeToggleCapsule(current_theme=self._theme, parent=self.sidebar)
        self.theme_toggle.setMinimumWidth(84)
        self.theme_toggle.setAccessibleName("Theme toggle")
        self.theme_toggle.setAccessibleDescription("Switch application theme between Sand Light and Vast Dark")
        self.theme_toggle.theme_toggled.connect(self.controller.set_theme)
        brand_row.addWidget(self.theme_toggle)
        sb_lay.addLayout(brand_row)
        sb_lay.addSpacing(8)

        # Quick Search Pill
        self.btn_global_search = QPushButton("Search VOX...   Ctrl+K")
        self.btn_global_search.setObjectName("sbSearchBtn")
        self.btn_global_search.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_global_search.setAccessibleName("Search VOX and Command Palette")
        self.btn_global_search.setAccessibleDescription("Open global command palette and search dialog, keyboard shortcut Control+K")
        self.btn_global_search.clicked.connect(self._open_global_search)
        sb_lay.addWidget(self.btn_global_search)
        sb_lay.addSpacing(8)

        # Primary Nav (0..6)
        nav_descriptions = [
            ("Home workspace", "Navigate to Home dictation history feed, keyboard shortcut Control+1"),
            ("Notetaker workspace", "Navigate to Notetaker meeting intelligence and recordings, keyboard shortcut Control+2"),
            ("Dictionary workspace", "Navigate to Dictionary and vocabulary replacements, keyboard shortcut Control+3"),
            ("Snippets workspace", "Navigate to Snippets keyword expansion triggers, keyboard shortcut Control+4"),
            ("Style workspace", "Navigate to Style and tone formatting profiles, keyboard shortcut Control+5"),
            ("Scratchpad workspace", "Navigate to Scratchpad notes and drafting canvas, keyboard shortcut Control+6"),
            ("Insights workspace", "Navigate to Insights speech statistics and usage analytics, keyboard shortcut Control+7"),
        ]
        for idx, ws in enumerate(self.WORKSPACES[:7]):
            btn = QPushButton(ws["name"])
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(36)
            if idx < len(nav_descriptions):
                btn.setAccessibleName(nav_descriptions[idx][0])
                btn.setAccessibleDescription(nav_descriptions[idx][1])
            btn.clicked.connect(lambda _=False, i=idx: self.navigate_to_workspace(i))
            self.nav_buttons.append(btn)
            sb_lay.addWidget(btn)
            if idx == 1:
                self.btn_calendar = QPushButton("Calendar")
                self.btn_calendar.setCheckable(True)
                self.btn_calendar.setCursor(Qt.CursorShape.PointingHandCursor)
                self.btn_calendar.setFixedHeight(36)
                self.btn_calendar.setAccessibleName("Calendar workspace")
                self.btn_calendar.setAccessibleDescription(
                    "Navigate to Calendar ecosystem, connected accounts, and upcoming meetings, keyboard shortcut Control+8"
                )
                self.btn_calendar.clicked.connect(lambda: self.navigate_to_workspace("calendar"))
                sb_lay.addWidget(self.btn_calendar)

        sb_lay.addStretch(1)

        # Utility Footer
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setObjectName("sbDivider")
        sb_lay.addWidget(sep)
        sb_lay.addSpacing(6)

        btn_settings = QPushButton("Settings")
        btn_settings.setCheckable(True)
        btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_settings.setFixedHeight(34)
        btn_settings.setAccessibleName("Settings workspace")
        btn_settings.setAccessibleDescription("Navigate to application settings and preferences, keyboard shortcut Control+Comma")
        btn_settings.clicked.connect(lambda: self.navigate_to_workspace(7))
        self.nav_buttons.append(btn_settings)
        sb_lay.addWidget(btn_settings)

        self.btn_open_scratchpad = QPushButton("Floating Scratchpad")
        self.btn_open_scratchpad.setObjectName("sbUtilBtn")
        self.btn_open_scratchpad.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_scratchpad.setAccessibleName("Open Floating Scratchpad")
        self.btn_open_scratchpad.setAccessibleDescription("Open the resizable floating scratchpad window for quick note-taking")
        self.btn_open_scratchpad.clicked.connect(self._emit_open_scratchpad)
        sb_lay.addWidget(self.btn_open_scratchpad)

        self.btn_open_drawer = QPushButton("Notetaker Drawer")
        self.btn_open_drawer.setObjectName("sbUtilBtn")
        self.btn_open_drawer.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_drawer.setAccessibleName("Open Notetaker Drawer")
        self.btn_open_drawer.setAccessibleDescription("Open the context-aware slide-out live notetaker drawer")
        self.btn_open_drawer.clicked.connect(self._emit_open_drawer)
        sb_lay.addWidget(self.btn_open_drawer)

        self.btn_replay_tour = QPushButton("Replay Tour")
        self.btn_replay_tour.setObjectName("sbUtilBtn")
        self.btn_replay_tour.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_replay_tour.setAccessibleName("Replay Onboarding Tour")
        self.btn_replay_tour.setAccessibleDescription("Launch the interactive onboarding tour and desktop feature walkthrough")
        self.btn_replay_tour.clicked.connect(self.replay_tour_requested.emit)
        sb_lay.addWidget(self.btn_replay_tour)

        self.btn_toggle_float = QPushButton("Hide Flow Bar")
        self.btn_toggle_float.setObjectName("sbUtilBtn")
        self.btn_toggle_float.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_float.setAccessibleName("Toggle Flow Bar")
        self.btn_toggle_float.setAccessibleDescription("Toggle visibility of the floating desktop Flow Bar pill")
        self.btn_toggle_float.clicked.connect(self._toggle_floating_bar)
        sb_lay.addWidget(self.btn_toggle_float)

        self.btn_check_updates = QPushButton("Check for Updates")
        self.btn_check_updates.setObjectName("sbUtilBtn")
        self.btn_check_updates.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_check_updates.setAccessibleName("Check for Updates")
        self.btn_check_updates.setAccessibleDescription("Check for latest VOX updates and improvements")
        self.btn_check_updates.clicked.connect(self._on_manual_check_updates)
        sb_lay.addWidget(self.btn_check_updates)

        # ---- Left Sidebar User Profile & Plan Status Card ----
        sb_lay.addStretch()

        self.plan_card = QFrame()
        self.plan_card.setObjectName("planStatusCard")
        plan_card_lay = QVBoxLayout(self.plan_card)
        plan_card_lay.setContentsMargins(12, 10, 12, 12)
        plan_card_lay.setSpacing(6)

        # 1. User Profile Row (Full width for avatar + name/email — never squeezed horizontally)
        profile_row = QHBoxLayout()
        profile_row.setSpacing(8)

        self.profile_avatar = QLabel("👤")
        self.profile_avatar.setObjectName("profileAvatar")
        self.profile_avatar.setFixedSize(28, 28)
        self.profile_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.profile_avatar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.profile_avatar.mousePressEvent = lambda e: self.show_settings_dialog(4)
        profile_row.addWidget(self.profile_avatar)

        profile_text_lay = QVBoxLayout()
        profile_text_lay.setSpacing(1)
        self.lbl_user_name = QLabel("Guest Mode")
        self.lbl_user_name.setObjectName("profileUserName")
        self.lbl_user_name.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.lbl_user_name.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_user_name.mousePressEvent = lambda e: self.show_settings_dialog(4)
        profile_text_lay.addWidget(self.lbl_user_name)

        self.lbl_user_email = QLabel("Offline on this PC")
        self.lbl_user_email.setObjectName("profileUserEmail")
        self.lbl_user_email.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.lbl_user_email.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_user_email.mousePressEvent = lambda e: self.show_settings_dialog(4)
        profile_text_lay.addWidget(self.lbl_user_email)
        profile_row.addLayout(profile_text_lay, 1)

        plan_card_lay.addLayout(profile_row)

        # 2. Google Sign-In Action (shown when not logged in)
        self.btn_google_sign_in = QPushButton("Sign In with Google")
        self.btn_google_sign_in.setObjectName("btnGoogleSignIn")
        self.btn_google_sign_in.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_google_sign_in.setFixedHeight(26)
        self.btn_google_sign_in.setAccessibleName("Sign In with Google to sync plan")
        self.btn_google_sign_in.clicked.connect(self._on_google_signin_clicked)
        plan_card_lay.addWidget(self.btn_google_sign_in)

        # 3. Divider
        self.profile_divider = QFrame()
        self.profile_divider.setObjectName("profileDivider")
        self.profile_divider.setFrameShape(QFrame.Shape.HLine)
        self.profile_divider.setFrameShadow(QFrame.Shadow.Sunken)
        self.profile_divider.setFixedHeight(1)
        plan_card_lay.addWidget(self.profile_divider)

        # 4. Plan Header Row
        plan_header_row = QHBoxLayout()
        plan_header_row.setSpacing(6)
        lbl_plan_title = QLabel("PLAN")
        lbl_plan_title.setObjectName("planSectionHeader")
        lbl_plan_title.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        plan_header_row.addWidget(lbl_plan_title)
        plan_header_row.addStretch()

        self.lbl_plan_badge = QLabel("VOX Free")
        self.lbl_plan_badge.setObjectName("planBadge")
        self.lbl_plan_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        plan_header_row.addWidget(self.lbl_plan_badge)
        plan_card_lay.addLayout(plan_header_row)

        self.lbl_quota_words = QLabel("Words: 0 / 3,000")
        self.lbl_quota_words.setObjectName("planQuotaLabel")
        plan_card_lay.addWidget(self.lbl_quota_words)

        self.bar_quota_words = QProgressBar()
        self.bar_quota_words.setObjectName("planQuotaProgress")
        self.bar_quota_words.setFixedHeight(6)
        self.bar_quota_words.setTextVisible(False)
        self.bar_quota_words.setRange(0, 3000)
        self.bar_quota_words.setValue(0)
        plan_card_lay.addWidget(self.bar_quota_words)

        self.btn_plan_upgrade = QPushButton("⚡ Upgrade to Pro")
        self.btn_plan_upgrade.setObjectName("planUpgradeBtn")
        self.btn_plan_upgrade.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_plan_upgrade.setFixedHeight(28)
        self.btn_plan_upgrade.setAccessibleName("Upgrade or manage plan")
        self.btn_plan_upgrade.clicked.connect(self._on_plan_button_clicked)
        plan_card_lay.addWidget(self.btn_plan_upgrade)

        self.btn_user_sign_out = QPushButton("Sign Out")
        self.btn_user_sign_out.setObjectName("btnUserSignOut")
        self.btn_user_sign_out.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_user_sign_out.setFixedHeight(24)
        self.btn_user_sign_out.setAccessibleName("Sign out of VOX account")
        self.btn_user_sign_out.clicked.connect(self._on_sign_out_clicked)
        self.btn_user_sign_out.setVisible(False)
        plan_card_lay.addWidget(self.btn_user_sign_out)

        sb_lay.addWidget(self.plan_card)
        sb_lay.addSpacing(4)

        self.lbl_privacy_badge = QLabel(f"v{__version__} · 100% Local AI · Offline")
        self.lbl_privacy_badge.setObjectName("sbMutedLabel")
        self.lbl_privacy_badge.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.lbl_privacy_badge.setAccessibleName("Privacy status indicator")
        self.lbl_privacy_badge.setAccessibleDescription("100% Local AI: all transcription and processing runs offline on this PC")
        sb_lay.addWidget(self.lbl_privacy_badge)

        # Explicit tab order chain for left sidebar
        self.setTabOrder(self.btn_global_search, self.nav_buttons[0])
        for i in range(len(self.nav_buttons) - 1):
            self.setTabOrder(self.nav_buttons[i], self.nav_buttons[i + 1])
        self.setTabOrder(self.nav_buttons[-1], self.btn_open_scratchpad)
        self.setTabOrder(self.btn_open_scratchpad, self.btn_open_drawer)
        self.setTabOrder(self.btn_open_drawer, self.btn_replay_tour)
        self.setTabOrder(self.btn_replay_tour, self.btn_toggle_float)
        self.setTabOrder(self.btn_toggle_float, self.btn_plan_upgrade)

        root.addWidget(self.sidebar)

        # ---- Right Workspace Stack ----
        labels = [ws["name"] for ws in self.WORKSPACES] + ["Calendar"]
        self.workspace_stack = VoxWorkspaceStack(labels=labels, parent=central)
        self.tab_widget = self.workspace_stack  # Legacy compatibility alias

        self.home_page = HomePage(self.controller, parent=self)
        self.home_page.navigate_requested.connect(self.navigate_to_workspace)
        self.home_page.status_toast.connect(self.show_toast)

        self.notetaker_page = NotetakerPage(self.controller, parent=self)
        self.notetaker_page.status_toast.connect(self.show_toast)
        self.notetaker_page.open_calendar_requested.connect(lambda: self.navigate_to_workspace("calendar"))
        self.notetaker_page.open_settings_requested.connect(self.show_settings_dialog)

        self.dictionary_page = DictionaryPage(self.controller, parent=self)
        self.dictionary_page.status_toast.connect(self.show_toast)

        self.snippets_page = SnippetsPage(self.controller, parent=self)
        self.snippets_page.status_toast.connect(self.show_toast)

        self.style_page = StylePage(self.controller, parent=self)
        self.style_page.status_toast.connect(self.show_toast)

        self.scratchpad_page = ScratchpadHubPage(self.controller, parent=self)
        self.scratchpad_page.status_toast.connect(self.show_toast)

        self.insights_page = InsightsPage(self.controller, parent=self)

        self.settings_page = SettingsWorkspaceWidget(self.controller, parent=self)
        self.settings_page.replay_tour_requested.connect(self.replay_tour_requested.emit)
        self.settings_page.status_toast.connect(self.show_toast)
        self.settings_page.open_calendar_requested.connect(lambda: self.navigate_to_workspace("calendar"))

        self.calendar_page = CalendarPage(self.controller, parent=self)
        self.calendar_page.status_toast.connect(self.show_toast)
        self.calendar_page.open_meeting_recording_requested.connect(self._on_open_recording_from_calendar)
        if hasattr(self.controller, "calendar_prepare_notetaker_requested"):
            self.controller.calendar_prepare_notetaker_requested.connect(self._on_prepare_notetaker_from_calendar)
        else:
            self.calendar_page.prepare_notetaker_requested.connect(self._on_prepare_notetaker_from_calendar)

        for page in (
            self.home_page,
            self.notetaker_page,
            self.dictionary_page,
            self.snippets_page,
            self.style_page,
            self.scratchpad_page,
            self.insights_page,
            self.settings_page,
            self.calendar_page,
        ):
            self.workspace_stack.addWidget(page)

        root.addWidget(self.workspace_stack, 1)
        self.navigate_to_workspace(0)
        self._update_float_btn_text()
        self._update_plan_card()

    def _setup_shortcuts(self) -> None:
        for idx, ws in enumerate(self.WORKSPACES):
            sc = QShortcut(QKeySequence(ws["shortcut"]), self)
            sc.activated.connect(lambda i=idx: self.navigate_to_workspace(i))
        QShortcut(QKeySequence("Ctrl+8"), self).activated.connect(lambda: self.navigate_to_workspace("calendar"))
        QShortcut(QKeySequence("Ctrl+K"), self).activated.connect(self._open_global_search)
        QShortcut(QKeySequence("Ctrl+T"), self).activated.connect(self.controller.toggle_theme)

    # ------------------------------------------------------------------
    # Navigation & Actions
    # ------------------------------------------------------------------
    def _resolve_workspace_index(self, target: Union[int, str], legacy_map: bool = False) -> int:
        if isinstance(target, str):
            clean = target.strip().lower().lstrip("/")
            if clean in self._ROUTE_TO_INDEX:
                return self._ROUTE_TO_INDEX[clean]
            try:
                idx = int(clean)
            except ValueError:
                return self.workspace_stack.currentIndex()
        else:
            idx = int(target)
        if legacy_map and idx in self._LEGACY_INT_TO_INDEX:
            return self._LEGACY_INT_TO_INDEX[idx]
        max_ws_idx = max(0, len(self.WORKSPACES) - 1)
        return max(0, min(idx, max_ws_idx))

    def navigate_to_workspace(self, target: Union[int, str], legacy_map: bool = False) -> None:
        idx = self._resolve_workspace_index(target, legacy_map=legacy_map)
        self.workspace_stack.setCurrentIndex(idx)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == idx)
        if hasattr(self, "btn_calendar"):
            self.btn_calendar.setChecked(idx == 8)
        if idx == 0:
            self.home_page.refresh_all()
        elif idx == 1:
            self.notetaker_page.refresh_all()
        elif idx == 2:
            self.dictionary_page.refresh()
        elif idx == 3:
            self.snippets_page.refresh()
        elif idx == 5:
            self.scratchpad_page.refresh_notes()
        elif idx == 6:
            self.insights_page.refresh()
        elif idx == 7:
            self.settings_page.load_from_config()
            if hasattr(self.settings_page, "_update_account_ui"):
                self.settings_page._update_account_ui()
        elif idx == 8:
            self.calendar_page.refresh()

    def _on_prepare_notetaker_from_calendar(self, meeting: object) -> None:
        self.navigate_to_workspace("notetaker")
        if hasattr(self.notetaker_page, "prepare_for_calendar_meeting"):
            self.notetaker_page.prepare_for_calendar_meeting(meeting)

    def _on_open_recording_from_calendar(self, recording_id: int) -> None:
        self.navigate_to_workspace("notetaker")
        if hasattr(self.notetaker_page, "select_meeting"):
            self.notetaker_page.select_meeting(int(recording_id))

    def open_and_focus_tab(self, tab_idx: Union[int, str] = 0, maximize: bool = False) -> None:
        use_legacy = isinstance(tab_idx, int)
        self.navigate_to_workspace(tab_idx, legacy_map=use_legacy)
        if maximize:
            self.showMaximized()
        else:
            self.show()
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def handle_meeting_saved_and_transcribed(self, meeting_id: int) -> None:
        """Safely invoked on the GUI thread via Qt QueuedConnection when a meeting is finalized."""
        try:
            if getattr(self.controller.config, "notetaker_auto_open_summary", True):
                self.open_and_focus_tab("notetaker")
                if hasattr(self.notetaker_page, "select_meeting"):
                    self.notetaker_page.select_meeting(int(meeting_id))
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug("handle_meeting_saved_and_transcribed notice: %s", exc)

    def show_settings_dialog(self, section_index: int = 0) -> None:
        self.open_and_focus_tab(7, maximize=False)
        self.settings_page.select_section(section_index)

    def _open_global_search(self) -> None:
        cmds = create_default_vox_commands(self, self.controller)
        dlg = VoxCommandPaletteDialog(
            parent=self,
            theme=self._theme,
            commands=cmds,
            controller=self.controller,
        )
        dlg.command_executed.connect(self.navigate_to_workspace)
        dlg.exec()

    def toggle_theme(self) -> None:
        if hasattr(self.controller, "toggle_theme"):
            self.controller.toggle_theme()
        else:
            nxt = "light" if self._theme == "dark" else "dark"
            self.apply_theme(nxt)

    def open_floating_scratchpad(self) -> None:
        self._emit_open_scratchpad()

    def open_notetaker_drawer(self) -> None:
        self._emit_open_drawer()

    def replay_onboarding_tour(self) -> None:
        self.replay_tour_requested.emit()

    def _on_search_jump(self, route: str, obj_id: object) -> None:
        self.navigate_to_workspace(route)
        if route == "notetaker" and isinstance(obj_id, int):
            self.notetaker_page.select_meeting(obj_id)

    def _emit_open_scratchpad(self) -> None:
        if hasattr(self.controller, "open_scratchpad_requested"):
            self.controller.open_scratchpad_requested.emit()

    def _emit_open_drawer(self) -> None:
        if hasattr(self.controller, "open_notetaker_drawer_requested"):
            self.controller.open_notetaker_drawer_requested.emit()

    def _toggle_floating_bar(self) -> None:
        fw = self.floating_widget or getattr(self.controller, "floating_pill", None)
        if fw is not None:
            if fw.isVisible():
                fw.hide()
            else:
                fw.show()
                fw.raise_()
            if self.controller and getattr(self.controller, "config", None):
                self.controller.config.show_floating_widget = fw.isVisible()
                self.controller.config.save()
        self._update_float_btn_text()

    def _update_float_btn_text(self) -> None:
        fw = self.floating_widget or getattr(self.controller, "floating_pill", None)
        if fw is not None and fw.isVisible():
            self.btn_toggle_float.setText("Hide Flow Bar")
        else:
            self.btn_toggle_float.setText("Show Flow Bar")

    # ------------------------------------------------------------------
    # Compatibility Ask-VOX Stream Handlers
    # ------------------------------------------------------------------
    def _on_ask_vox_token(self, query_id: int, token: str) -> None:
        if query_id != getattr(self, "_current_ask_vox_id", 0) or not self._ask_vox_is_streaming:
            return
        self._ask_vox_buffer += token

    def _on_ask_vox_citations(self, query_id: int, citations: list) -> None:
        if query_id != getattr(self, "_current_ask_vox_id", 0):
            return
        self.citations_list.clear()
        for cit in citations:
            st = float(cit.get("start_time", 0.0))
            txt = str(cit.get("text") or "")
            item = QListWidgetItem(f"[{format_seconds_hms(st)}] {cit.get('meeting_title', 'Meeting')}: {txt}")
            self.citations_list.addItem(item)

    def _on_ask_vox_finished(self, query_id: int, res: dict) -> None:
        if query_id != getattr(self, "_current_ask_vox_id", 0):
            return
        self._ask_vox_is_streaming = False
        if hasattr(self, "txt_ask_answer") and getattr(self, "txt_ask_answer") is not None:
            final_answer = (self._ask_vox_buffer or "").strip() or res.get("answer", "")
            self.txt_ask_answer.setMarkdown(final_answer)
        if self.citations_list.count() == 0 and res.get("citations"):
            self._on_ask_vox_citations(query_id, res["citations"])

    # ------------------------------------------------------------------
    # Theme & Toast Notifications
    # ------------------------------------------------------------------
    def apply_theme(self, theme_name: str) -> None:
        self._theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        t = get_theme_tokens(self._theme)
        self.setWindowIcon(create_app_icon(size=64, theme=self._theme))
        self.lbl_logo_mark.setPixmap(create_app_pixmap(size=28, theme=self._theme))

        if hasattr(self, "theme_toggle"):
            self.theme_toggle.set_theme(self._theme)

        self.setStyleSheet(
            f"""
            QMainWindow {{ background-color: {t['bg']}; color: {t['text']}; }}
            QWidget {{ color: {t['text']}; }}
            """
        )
        self.sidebar.setStyleSheet(
            f"""
            QFrame#leftSidebar {{ background-color: {t['sidebar_bg']}; border-right: 1px solid {t['line']}; }}
            QLabel {{ color: {t['text']}; border: none; background: transparent; }}
            QLabel#sbMutedLabel {{ color: {t['text_dim']}; font-size: 11px; padding: 6px 8px 0 8px; }}
            QFrame#sbDivider, QFrame#profileDivider {{ background-color: {t['line']}; border: none; max-height: 1px; }}
            QPushButton {{
                text-align: left;
                padding: 7px 12px;
                border-radius: 8px;
                border: none;
                background: transparent;
                color: {t['text_secondary']};
                font-size: 13.5px;
                font-weight: 500;
            }}
            QPushButton:hover {{ background-color: {t['sidebar_hover']}; color: {t['text']}; }}
            QPushButton:checked {{
                background-color: {t['sidebar_active']};
                color: {t['text']};
                font-weight: 700;
            }}
            QPushButton#sbSearchBtn {{
                background-color: {t['surface']};
                border: 1px solid {t['border']};
                color: {t['text_muted']};
                font-size: 12px;
                padding: 7px 10px;
            }}
            QPushButton#sbUtilBtn {{
                font-size: 12px;
                padding: 5px 10px;
                color: {t['text_muted']};
            }}
            QFrame#planStatusCard {{
                background-color: {t['surface']};
                border: 1px solid {t['border']};
                border-radius: 10px;
            }}
            QLabel#planSectionHeader {{
                color: {t['text_dim']};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.8px;
            }}
            QLabel#planQuotaLabel {{
                color: {t['text_secondary']};
                font-size: 11px;
                font-weight: 500;
            }}
            QLabel#profileUserName {{
                color: {t['text_primary']};
                font-size: 12px;
                font-weight: 700;
            }}
            QLabel#profileUserEmail {{
                color: {t['text_muted']};
                font-size: 10px;
                font-weight: 400;
            }}
            QPushButton#btnGoogleSignIn {{
                text-align: center;
                background-color: {t['surface_well']};
                color: {t['text_primary']};
                border: 1px solid {t['border']};
                border-radius: 6px;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 8px;
            }}
            QPushButton#btnGoogleSignIn:hover {{
                background-color: {t['surface_secondary']};
                border-color: {t['accent']};
            }}
            QPushButton#btnUserSignOut {{
                text-align: center;
                background-color: {t['surface_well']};
                color: {t['text_secondary']};
                border: 1px solid {t['border']};
                border-radius: 6px;
                font-size: 11px;
                font-weight: 600;
                padding: 3px 8px;
            }}
            QPushButton#btnUserSignOut:hover {{
                background-color: {t['surface_secondary']};
                color: #EF4444;
                border-color: #EF4444;
            }}
            """
        )

        for page in (
            self.home_page,
            self.notetaker_page,
            self.dictionary_page,
            self.snippets_page,
            self.style_page,
            self.scratchpad_page,
            self.insights_page,
            self.settings_page,
            self.calendar_page,
        ):
            if hasattr(page, "apply_theme"):
                page.apply_theme(self._theme)

        self._update_plan_card()

    set_theme = apply_theme
    _apply_theme = apply_theme

    def show_toast(self, title: str, message: str) -> None:
        if not self.isVisible():
            return
        for existing in self._toasts:
            if getattr(existing, "toast_title", "") == title:
                if hasattr(existing, "update_message"):
                    existing.update_message(message)
                return
        toast = ToastNotification(title, message, parent=self, theme=self._theme)
        self._toasts.append(toast)
        toast.closed.connect(lambda: self._remove_toast(toast))
        toast.adjustSize()
        margin = 18
        y_offset = sum(t.height() + 8 for t in self._toasts[:-1])
        x = max(margin, self.width() - toast.width() - margin)
        y = max(margin, self.height() - toast.height() - margin - y_offset)
        toast.move(x, y)
        toast.show()

    def _remove_toast(self, toast: ToastNotification) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)

    def _on_plan_button_clicked(self) -> None:
        try:
            account = self.controller.get_account_status() if hasattr(self.controller, "get_account_status") else {}
        except Exception:
            account = {}
        if not isinstance(account, dict) or not account.get("signed_in"):
            from PyQt6.QtWidgets import QMessageBox
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Sign in to VOX")
            msg_box.setText("Please sign in with your Google account first.")
            msg_box.setInformativeText(
                "Signing in ensures your VOX Pro subscription is permanently linked to your profile "
                "and accessible across all your devices."
            )
            btn_signin = msg_box.addButton("Continue with Google", QMessageBox.ButtonRole.AcceptRole)
            btn_cancel = msg_box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            msg_box.setDefaultButton(btn_signin)
            msg_box.exec()
            if msg_box.clickedButton() == btn_signin:
                self._on_google_signin_clicked()
            return

        effective_ent = self.controller.entitlement.get_effective_entitlement() if hasattr(self.controller, "entitlement") else {}
        is_pro = bool(account.get("is_pro", False)) or bool(effective_ent.get("is_pro", False))
        if is_pro:
            self.show_settings_dialog(4)
            self.show_toast(
                "Account & Subscription",
                "Account & Subscription — Manage your VOX Pro plan and connected accounts.",
            )
            return

        reason = "Upgrade to VOX Pro"
        if hasattr(self.controller, "request_upgrade_dialog"):
            self.controller.request_upgrade_dialog(reason=reason)
        else:
            self._show_pro_upgrade_dialog(reason=reason)

    def _on_google_signin_clicked(self) -> None:
        """Launches the Google OAuth flow in the user's browser."""
        self.show_toast("Connecting...", "Opening browser for Google Authentication...")
        if hasattr(self.controller, "login_with_google"):
            self.controller.login_with_google()

    def _on_login_completed(self, ok: bool, msg: str) -> None:
        """Handled on the Qt GUI thread via queued signal connection."""
        if ok:
            self.show_toast("Signed In", "Welcome to VOX! Account synchronized.")
        else:
            self.show_toast("Sign-In", msg)

    def _on_manual_check_updates(self) -> None:
        """User clicked 'Check for Updates' in sidebar."""
        from voxlocal import __version__
        self.show_toast("Checking for Updates", "Connecting to VOX update server...")
        def _bg():
            is_avail, manifest, msg = self.controller.check_for_updates(timeout_sec=10)
            if not is_avail:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(
                    0,
                    lambda: self.show_toast(
                        "VOX Up to Date",
                        msg or f"You are using the latest version of VOX (v{__version__}).",
                    ),
                )
        import threading
        threading.Thread(target=_bg, daemon=True, name="ManualUpdateCheck").start()

    def _on_model_status_changed(self, msg: str) -> None:
        if not msg:
            return
        m_lower = msg.lower()
        if "ready" in m_lower or "loaded" in m_lower:
            self.show_toast("AI Engine Ready", msg)
        elif "downloading" in m_lower or "bootstrapping" in m_lower or "loading" in m_lower:
            self.show_toast("AI Speech Engine", msg)

    def _on_update_available(self, is_avail: bool, manifest: Dict[str, Any], msg: str) -> None:
        """Display an in-app update notification when a new version is detected."""
        if not is_avail or not manifest:
            return
        version = str(manifest.get("version", "Latest"))
        notes = str(manifest.get("release_notes") or "New performance improvements and features.")
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle(f"VOX Update Available (v{version})")
        box.setText(f"A new version of VOX (v{version}) is ready to install!")
        box.setInformativeText(f"{notes}\n\nWould you like to update VOX now?")
        btn_update = box.addButton("⚡ Update Now", QMessageBox.ButtonRole.AcceptRole)
        btn_later = box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_update)
        box.exec()
        if box.clickedButton() == btn_update:
            self._start_in_app_update(manifest)

    def _start_in_app_update(self, manifest: Dict[str, Any]) -> None:
        """Downloads the cryptographically verified installer in background and restarts."""
        from voxlocal import __version__
        if getattr(self.controller, "capture_state", "idle") in ("recording", "paused"):
            defer_msg = (
                "An active meeting recording is currently in progress. "
                "VOX will defer installing this update until your meeting finishes."
            )
            self.show_toast("Update Deferred", defer_msg)
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Meeting Recording in Progress", defer_msg)
            return

        ver = str(manifest.get("version", __version__))
        self.show_toast("Downloading Update...", f"Fetching VOX v{ver} in background...")

        def _bg_downloader():
            import tempfile
            dest_dir = Path(tempfile.gettempdir()) / "vox_updater"
            dest_dir.mkdir(parents=True, exist_ok=True)
            installer_dest = dest_dir / f"VoxLocal-Setup-{ver}.exe"
            
            mgr = getattr(self.controller, "update_manager", None)
            if not mgr:
                from voxlocal.core.updater import UpdateManager
                mgr = UpdateManager()

            ok, dl_msg = mgr.download_and_verify(manifest, installer_dest)
            if ok and installer_dest.exists():
                logger.info("Update downloaded and verified: %s", installer_dest)
                from PyQt6.QtCore import QTimer

                def _apply_and_restart():
                    self.show_toast("Installing Update", "Restarting VOX to complete installation...")
                    import subprocess
                    subprocess.Popen([str(installer_dest), "--silent"])
                    import sys
                    from PyQt6.QtWidgets import QApplication
                    QApplication.instance().quit()

                QTimer.singleShot(400, _apply_and_restart)
            else:
                logger.warning("Auto-update failed: %s", dl_msg)
                if hasattr(self.controller, "toast_requested"):
                    self.controller.toast_requested.emit("Update Notice", f"Auto-update could not complete: {dl_msg}")

        import threading
        threading.Thread(target=_bg_downloader, daemon=True, name="InAppAutoUpdater").start()

    def _on_sign_out_clicked(self) -> None:
        """Logs out the active user and reverts to Guest Free mode."""
        if hasattr(self.controller, "logout_user"):
            self.controller.logout_user()
            self.show_toast("Signed Out", "Reverted to Guest mode.")

    def _update_plan_card(
        self,
        entitlement_data: Optional[Dict[str, Any]] = None,
        usage_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update User Profile, Plan Badge, Word Quota Meter, and Upgrade button in sidebar."""
        if not hasattr(self, "plan_card"):
            return
        try:
            t = get_theme_tokens(self._theme)

            self.plan_card.setStyleSheet(
                f"QFrame#planStatusCard {{ "
                f"background-color: {t['surface']}; "
                f"border: 1px solid {t['border']}; "
                f"border-radius: 10px; "
                f"}}"
            )
            if hasattr(self, "btn_user_sign_out"):
                self.btn_user_sign_out.setStyleSheet(
                    f"QPushButton#btnUserSignOut {{ "
                    f"text-align: center; "
                    f"background-color: {t['surface_well']}; "
                    f"color: {t['text_secondary']}; "
                    f"border: 1px solid {t['border']}; "
                    f"border-radius: 6px; "
                    f"font-size: 11px; "
                    f"font-weight: 600; "
                    f"padding: 3px 8px; "
                    f"}} "
                    f"QPushButton#btnUserSignOut:hover {{ "
                    f"background-color: {t['surface_secondary']}; "
                    f"color: #EF4444; "
                    f"border-color: #EF4444; "
                    f"}}"
                )

            # 1. Update User Profile Section
            account_status = (
                self.controller.get_account_status()
                if hasattr(self.controller, "get_account_status")
                else {}
            )
            signed_in = bool(account_status.get("signed_in", False))
            user_name = account_status.get("name") or "Guest User"
            user_email = account_status.get("email") or ""

            if hasattr(self, "lbl_user_name"):
                if signed_in and user_email:
                    display_name = user_name if user_name != "Guest User" else user_email.split("@")[0]
                    # Format display text cleanly with ellipsis across full card width (145 px)
                    fm = self.lbl_user_name.fontMetrics()
                    elided_name = fm.elidedText(display_name, Qt.TextElideMode.ElideRight, 145)
                    self.lbl_user_name.setText(elided_name)
                    self.lbl_user_name.setToolTip(f"{user_name}\n{user_email}\n(Click to view Account & Subscription)")

                    fm_email = self.lbl_user_email.fontMetrics()
                    elided_email = fm_email.elidedText(user_email, Qt.TextElideMode.ElideRight, 145)
                    self.lbl_user_email.setText(elided_email)
                    self.lbl_user_email.setToolTip(user_email)
                    self.lbl_user_email.setVisible(True)

                    initial = (display_name or user_email or "U")[0].upper()
                    self.profile_avatar.setText(initial)
                    self.profile_avatar.setStyleSheet(
                        f"background: {t['accent']}; color: #FFFFFF; "
                        f"font-weight: 800; font-size: 12px; border-radius: 14px;"
                    )
                    if hasattr(self, "btn_google_sign_in"):
                        self.btn_google_sign_in.setVisible(False)
                    if hasattr(self, "btn_user_sign_out"):
                        self.btn_user_sign_out.setVisible(True)
                else:
                    self.lbl_user_name.setText("Guest Mode")
                    self.lbl_user_email.setText("Offline on this PC")
                    self.lbl_user_email.setVisible(True)
                    self.profile_avatar.setText("👤")
                    self.profile_avatar.setStyleSheet(
                        f"background: {t['surface_well']}; color: {t['text_muted']}; "
                        f"font-size: 13px; border-radius: 14px;"
                    )
                    if hasattr(self, "btn_google_sign_in"):
                        self.btn_google_sign_in.setVisible(True)
                    if hasattr(self, "btn_user_sign_out"):
                        self.btn_user_sign_out.setVisible(False)

            # 2. Entitlement & Usage update
            ent = entitlement_data or (
                self.controller.entitlement.get_effective_entitlement()
                if hasattr(self.controller, "entitlement")
                else {}
            )
            usage = usage_data or (
                self.controller.metering.get_weekly_usage()
                if hasattr(self.controller, "metering")
                else {}
            )
            from voxlocal.core.entitlement import is_master_developer
            is_master = is_master_developer(email=user_email or account_status.get("email"))
            is_pro = is_master or bool(ent.get("is_pro", False)) or bool(account_status.get("is_pro", False))
            if not signed_in or not user_email:
                if (
                    entitlement_data is not None
                    and bool(entitlement_data.get("is_pro", False))
                    and entitlement_data.get("user_id") not in ("default", "guest-device")
                ):
                    is_pro = True
                else:
                    is_pro = False
            words_used = int(usage.get("dictation_words", 0))

            limits = ent.get("limits") or {}
            raw_word_limit = limits.get("dictation_words_weekly")
            word_limit = int(raw_word_limit) if raw_word_limit is not None else 3000

            t = get_theme_tokens(self._theme)

            if is_pro:
                self.lbl_plan_badge.setText("👑 VOX Pro")
                self.lbl_plan_badge.setStyleSheet(
                    f"background: {t['accent_emerald_tint']}; color: {t['accent_emerald']}; "
                    f"border: 1px solid {t['accent_emerald']}; border-radius: 999px; "
                    f"padding: 2px 8px; font-size: 11px; font-weight: 700;"
                )
                self.lbl_quota_words.setText("Words: Unlimited")
                self.bar_quota_words.setRange(0, 100)
                self.bar_quota_words.setValue(100)
                self.bar_quota_words.setStyleSheet(
                    f"QProgressBar {{ border: none; background: {t['surface_well']}; border-radius: 3px; }} "
                    f"QProgressBar::chunk {{ background: {t['accent_emerald']}; border-radius: 3px; }}"
                )
                self.btn_plan_upgrade.setText("👑 Manage Plan")
                self.btn_plan_upgrade.setStyleSheet(
                    f"QPushButton#planUpgradeBtn {{ "
                    f"text-align: center; "
                    f"background: transparent; color: {t['accent_emerald']}; "
                    f"border: 1px solid {t['accent_emerald']}; border-radius: 6px; "
                    f"font-size: 11.5px; font-weight: 600; padding: 4px 8px; "
                    f"}} "
                    f"QPushButton#planUpgradeBtn:hover {{ "
                    f"background: {t['accent_emerald_tint']}; "
                    f"}}"
                )
            else:
                self.lbl_plan_badge.setText("VOX Free")
                self.lbl_plan_badge.setStyleSheet(
                    f"background: {t['surface_secondary']}; color: {t['text_muted']}; "
                    f"border: 1px solid {t['border']}; border-radius: 999px; "
                    f"padding: 2px 8px; font-size: 11px; font-weight: 700;"
                )
                self.lbl_quota_words.setText(f"Words: {words_used:,} / {word_limit:,}")
                self.bar_quota_words.setRange(0, word_limit)
                self.bar_quota_words.setValue(min(word_limit, words_used))

                if words_used >= word_limit:
                    chunk_color = "#EF4444"
                elif words_used >= word_limit * 0.8:
                    chunk_color = t.get("accent_gold", "#D97706")
                else:
                    chunk_color = t["accent"]

                self.bar_quota_words.setStyleSheet(
                    f"QProgressBar {{ border: none; background: {t['surface_well']}; border-radius: 3px; }} "
                    f"QProgressBar::chunk {{ background: {chunk_color}; border-radius: 3px; }}"
                )
                self.btn_plan_upgrade.setText("⚡ Upgrade to Pro")
                self.btn_plan_upgrade.setStyleSheet(
                    f"QPushButton#planUpgradeBtn {{ "
                    f"text-align: center; "
                    f"background: {t['accent']}; color: #FFFFFF; "
                    f"border: none; border-radius: 6px; "
                    f"font-size: 11.5px; font-weight: 700; padding: 4px 8px; "
                    f"}} "
                    f"QPushButton#planUpgradeBtn:hover {{ "
                    f"background: {t.get('accent_fathom_hover', '#4338CA')}; "
                    f"}}"
                )
        except Exception as exc:
            logger.debug("Failed updating Plan Status Card: %s", exc)

    _update_plan_status_card = _update_plan_card

    def _show_pro_upgrade_dialog(self, reason: str = "") -> None:
        """Show the modern 2026 VOX Pro upgrade modal."""
        try:
            from voxlocal.ui.pro_upgrade_dialog import VoxProUpgradeDialog
            dlg = VoxProUpgradeDialog(self.controller, reason=reason, theme=self._theme, parent=self)
            dlg.exec()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Failed to show Pro Upgrade Dialog: %s", exc)

    def prepare_force_quit(self) -> None:
        self._force_quit = True

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._force_quit:
            event.accept()
            return
        event.ignore()
        self.hide()

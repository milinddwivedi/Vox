"""VOX Calendar Connect Dialog — Microsoft Outlook, Google Calendar & Zoom."""

from __future__ import annotations

import threading
import webbrowser
from typing import Any, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from voxlocal.calendar.auth_service import sanitize_oauth_error_for_user
from voxlocal.core.logger import get_logger
from voxlocal.ui.theme import get_dialog_stylesheet, get_theme_tokens, get_ui_font

logger = get_logger("ui.calendar_connect_dialog")


class VoxCalendarConnectDialog(QDialog):
    """
    Unified multi-provider calendar connection dialog supporting:
    1. Microsoft Outlook / Microsoft 365 (RFC 8628 Device Code for WFX Work Accounts & Loopback PKCE)
    2. Google Calendar (OAuth 2.0 PKCE & Calendar API v3)
    3. Zoom Meetings (OAuth 2.0 & Meetings API 24h/6-month sync)
    4. Outlook ICS Feed / Local MAPI
    """

    connected = pyqtSignal(str)  # (summary_message)
    _async_result = pyqtSignal(bool, str)
    _device_code_received = pyqtSignal(str, str)  # (user_code, verification_uri)

    def __init__(
        self,
        controller: Any,
        theme: str = "light",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._theme = "dark" if (theme or "").lower() == "dark" else "light"
        self._cancel_event: Optional[threading.Event] = None
        self._connected_msg: str = ""
        self._busy: bool = False

        self.setWindowTitle("Connect Calendar & Meetings — VOX")
        self.setModal(True)
        self.setMinimumSize(700, 520)
        self.resize(720, 560)
        self._async_result.connect(self._on_worker_finished)
        self._device_code_received.connect(self._on_device_code_received)

        self._build_ui()
        self.apply_theme(self._theme)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 22)
        root.setSpacing(14)

        lbl_eye = QLabel("CALENDAR & MEETINGS")
        lbl_eye.setObjectName("onbEyebrow")
        root.addWidget(lbl_eye)

        self.lbl_title = QLabel("Connect Your Meeting Calendars")
        self.lbl_title.setObjectName("onbStepTitle")
        self.lbl_title.setFont(get_ui_font(22, 500, serif=True))
        self.lbl_title.setWordWrap(True)
        root.addWidget(self.lbl_title)

        self.lbl_sub = QLabel(
            "Connect your personal or work accounts to see upcoming meetings, identify Teams, Zoom, and Google Meet links, "
            "and prepare Notetaker automatically. Connecting another account adds it alongside your existing calendars."
        )
        self.lbl_sub.setWordWrap(True)
        self.lbl_sub.setObjectName("onbSub")
        root.addWidget(self.lbl_sub)

        self.perm_banner = QFrame()
        self.perm_banner.setObjectName("voxWellCard")
        perm_lay = QHBoxLayout(self.perm_banner)
        perm_lay.setContentsMargins(12, 8, 12, 8)
        self.lbl_permissions = QLabel(
            "Read-only permissions: VOX reads your calendar events to display schedules and detect calls. "
            "Recordings and transcripts stay private and local."
        )
        self.lbl_permissions.setWordWrap(True)
        self.lbl_permissions.setStyleSheet("font-size: 11px;")
        perm_lay.addWidget(self.lbl_permissions)
        root.addWidget(self.perm_banner)

        # Tab navigation row
        nav_row = QHBoxLayout()
        nav_row.setSpacing(8)
        self._tab_buttons: list[QPushButton] = []
        tabs = [
            ("Microsoft Outlook", "Connect Microsoft Outlook calendar"),
            ("Google Calendar", "Connect Google Calendar and Google Meet"),
            ("Zoom Meetings", "Connect Zoom account for upcoming & scheduled calls"),
            ("Calendar Feed (.ics)", "Published calendar feed or desktop Outlook"),
        ]
        for idx, (label, tooltip) in enumerate(tabs):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, i=idx: self._select_tab(i))
            nav_row.addWidget(btn)
            self._tab_buttons.append(btn)
        nav_row.addStretch(1)
        root.addLayout(nav_row)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # -------------------------------------------------------------
        # Tab 0: Microsoft Outlook
        # -------------------------------------------------------------
        tab_ms = QFrame()
        tab_ms.setObjectName("voxCard")
        ms_lay = QVBoxLayout(tab_ms)
        ms_lay.setContentsMargins(18, 16, 18, 16)
        ms_lay.setSpacing(12)

        lbl_ms_hdr = QLabel("Microsoft Outlook & Microsoft 365")
        lbl_ms_hdr.setStyleSheet("font-size: 13px; font-weight: 600;")
        ms_lay.addWidget(lbl_ms_hdr)

        lbl_ms_info = QLabel(
            "Supports personal Outlook (@outlook.com, @hotmail.com, @live.com) as well as "
            "corporate Microsoft 365 and school Entra ID accounts."
        )
        lbl_ms_info.setWordWrap(True)
        ms_lay.addWidget(lbl_ms_info)

        self.txt_ms_account_hint = QLineEdit(str(getattr(self.controller.config, "calendar_account_email", "") or ""))
        self.txt_ms_account_hint.setPlaceholderText("Email address (optional — leave blank to choose account in browser)")
        ms_lay.addWidget(self.txt_ms_account_hint)

        # Device code card (hidden until requested)
        self.code_card = QFrame()
        self.code_card.setObjectName("voxWellCard")
        self.code_card.setVisible(False)
        cc_lay = QVBoxLayout(self.code_card)
        cc_lay.setContentsMargins(16, 12, 16, 12)
        cc_lay.setSpacing(8)

        lbl_cc_desc = QLabel("Enter this code in your browser to approve VOX access:")
        lbl_cc_desc.setStyleSheet("font-size: 12px;")
        cc_lay.addWidget(lbl_cc_desc)

        self.lbl_user_code = QLabel("---- ----")
        self.lbl_user_code.setFont(get_ui_font(24, 700))
        self.lbl_user_code.setStyleSheet("letter-spacing: 2px; color: #1a73e8;")
        self.lbl_user_code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cc_lay.addWidget(self.lbl_user_code)

        cc_act = QHBoxLayout()
        self.btn_copy_and_open = QPushButton("Copy Code & Open Microsoft Login")
        self.btn_copy_and_open.setObjectName("dawnBtn")
        self.btn_copy_and_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_and_open.clicked.connect(self._copy_code_and_open_url)
        cc_act.addWidget(self.btn_copy_and_open)

        self.lbl_poll_status = QLabel("Waiting for approval...")
        self.lbl_poll_status.setStyleSheet("font-size: 11px; font-style: italic;")
        cc_act.addWidget(self.lbl_poll_status, 1)
        cc_lay.addLayout(cc_act)
        ms_lay.addWidget(self.code_card)

        # Action row
        ms_act = QHBoxLayout()
        self.btn_ms_start = QPushButton("Connect Microsoft Outlook")
        self.btn_ms_start.setObjectName("fathomBtn")
        self.btn_ms_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ms_start.clicked.connect(self._start_ms_device_code)
        ms_act.addWidget(self.btn_ms_start)

        self.btn_ms_pkce = QPushButton("Sign in via Browser (PKCE)")
        self.btn_ms_pkce.setObjectName("secondaryBtn")
        self.btn_ms_pkce.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ms_pkce.setVisible(False)
        self.btn_ms_pkce.clicked.connect(self._start_ms_browser_pkce)
        ms_act.addWidget(self.btn_ms_pkce)

        self.btn_ms_cancel = QPushButton("Cancel")
        self.btn_ms_cancel.setObjectName("ghostBtn")
        self.btn_ms_cancel.setVisible(False)
        self.btn_ms_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ms_cancel.clicked.connect(self._cancel_pending)
        ms_act.addWidget(self.btn_ms_cancel)
        ms_act.addStretch(1)
        ms_lay.addLayout(ms_act)

        # Advanced OAuth configuration (optional)
        lbl_ms_adv = QLabel("Optional: Custom App Registration Credentials")
        lbl_ms_adv.setStyleSheet("font-size: 11px; font-weight: 500; margin-top: 8px;")
        ms_lay.addWidget(lbl_ms_adv)

        adv_row = QHBoxLayout()
        adv_row.setSpacing(8)
        self.txt_ms_client_id = QLineEdit(str(getattr(self.controller.config, "calendar_client_id", "") or ""))
        self.txt_ms_client_id.setPlaceholderText("Custom Client ID (leave blank for default)")
        self.txt_ms_client_id.textChanged.connect(lambda t: self.btn_ms_pkce.setVisible(bool(t.strip())))
        self.btn_ms_pkce.setVisible(bool(self.txt_ms_client_id.text().strip()))
        self.txt_ms_tenant_id = QLineEdit(str(getattr(self.controller.config, "calendar_tenant_id", "common") or "common"))
        self.txt_ms_tenant_id.setFixedWidth(140)
        self.txt_ms_tenant_id.setPlaceholderText("Tenant ID (common)")
        adv_row.addWidget(self.txt_ms_client_id, 1)
        adv_row.addWidget(self.txt_ms_tenant_id)
        ms_lay.addLayout(adv_row)

        ms_lay.addStretch(1)
        self.stack.addWidget(tab_ms)

        # -------------------------------------------------------------
        # Tab 1: Google Calendar
        # -------------------------------------------------------------
        tab_google = QFrame()
        tab_google.setObjectName("voxCard")
        g_lay = QVBoxLayout(tab_google)
        g_lay.setContentsMargins(18, 16, 18, 16)
        g_lay.setSpacing(12)

        lbl_g_hdr = QLabel("Google Calendar & Google Meet")
        lbl_g_hdr.setStyleSheet("font-size: 13px; font-weight: 600;")
        g_lay.addWidget(lbl_g_hdr)

        lbl_g_info = QLabel(
            "Connect Google Calendar using OAuth 2.0 PKCE or an iCal (.ics) address. VOX synchronizes "
            "upcoming calendar events, identifies Google Meet video links, and extracts Teams and Zoom URLs."
        )
        lbl_g_info.setWordWrap(True)
        g_lay.addWidget(lbl_g_info)

        g_creds_lbl = QLabel("Google OAuth Client ID or iCal (.ics) Address:")
        g_creds_lbl.setStyleSheet("font-size: 11px; font-weight: 500;")
        g_lay.addWidget(g_creds_lbl)

        g_row1 = QHBoxLayout()
        self.txt_google_client_id = QLineEdit(str(getattr(self.controller.config, "google_calendar_client_id", "") or ""))
        self.txt_google_client_id.setPlaceholderText("Google OAuth Client ID or https://calendar.google.com/.../basic.ics")
        g_row1.addWidget(self.txt_google_client_id)
        g_lay.addLayout(g_row1)

        g_row2 = QHBoxLayout()
        self.txt_google_client_secret = QLineEdit(str(getattr(self.controller.config, "google_calendar_client_secret", "") or ""))
        self.txt_google_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_google_client_secret.setPlaceholderText("Google OAuth Client Secret (optional for PKCE)")
        g_row2.addWidget(self.txt_google_client_secret)
        g_lay.addLayout(g_row2)

        g_act = QHBoxLayout()
        self.btn_google_start = QPushButton("Sign in with Google")
        self.btn_google_start.setObjectName("fathomBtn")
        self.btn_google_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_google_start.clicked.connect(self._start_google_oauth)
        g_act.addWidget(self.btn_google_start)

        self.btn_google_cancel = QPushButton("Cancel")
        self.btn_google_cancel.setObjectName("ghostBtn")
        self.btn_google_cancel.setVisible(False)
        self.btn_google_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_google_cancel.clicked.connect(self._cancel_pending)
        g_act.addWidget(self.btn_google_cancel)
        g_act.addStretch(1)
        g_lay.addLayout(g_act)

        g_lay.addStretch(1)
        self.stack.addWidget(tab_google)

        # -------------------------------------------------------------
        # Tab 2: Zoom Meetings
        # -------------------------------------------------------------
        tab_zoom = QFrame()
        tab_zoom.setObjectName("voxCard")
        z_lay = QVBoxLayout(tab_zoom)
        z_lay.setContentsMargins(18, 16, 18, 16)
        z_lay.setSpacing(12)

        lbl_z_hdr = QLabel("Zoom Meetings Integration")
        lbl_z_hdr.setStyleSheet("font-size: 13px; font-weight: 600;")
        z_lay.addWidget(lbl_z_hdr)

        lbl_z_info = QLabel(
            "Connect your Zoom account to synchronize all upcoming meetings. VOX queries both the immediate 24-hour "
            "and scheduled meetings endpoints (supporting up to 6 months forward) and deduplicates matching events."
        )
        lbl_z_info.setWordWrap(True)
        z_lay.addWidget(lbl_z_info)

        z_creds_lbl = QLabel("Zoom OAuth App Credentials (Optional if pre-configured):")
        z_creds_lbl.setStyleSheet("font-size: 11px; font-weight: 500;")
        z_lay.addWidget(z_creds_lbl)

        z_row1 = QHBoxLayout()
        self.txt_zoom_client_id = QLineEdit(str(getattr(self.controller.config, "zoom_client_id", "") or ""))
        self.txt_zoom_client_id.setPlaceholderText("Zoom OAuth Client ID")
        z_row1.addWidget(self.txt_zoom_client_id)
        z_lay.addLayout(z_row1)

        z_row2 = QHBoxLayout()
        self.txt_zoom_client_secret = QLineEdit(str(getattr(self.controller.config, "zoom_client_secret", "") or ""))
        self.txt_zoom_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_zoom_client_secret.setPlaceholderText("Zoom OAuth Client Secret")
        z_row2.addWidget(self.txt_zoom_client_secret)
        z_lay.addLayout(z_row2)

        z_act = QHBoxLayout()
        self.btn_zoom_start = QPushButton("Connect Zoom Account")
        self.btn_zoom_start.setObjectName("fathomBtn")
        self.btn_zoom_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_zoom_start.clicked.connect(self._start_zoom_oauth)
        z_act.addWidget(self.btn_zoom_start)

        self.btn_zoom_cancel = QPushButton("Cancel")
        self.btn_zoom_cancel.setObjectName("ghostBtn")
        self.btn_zoom_cancel.setVisible(False)
        self.btn_zoom_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_zoom_cancel.clicked.connect(self._cancel_pending)
        z_act.addWidget(self.btn_zoom_cancel)
        z_act.addStretch(1)
        z_lay.addLayout(z_act)

        z_lay.addStretch(1)
        self.stack.addWidget(tab_zoom)

        # -------------------------------------------------------------
        # Tab 3: Calendar Feed (.ics) / Desktop MAPI
        # -------------------------------------------------------------
        tab_ics = QFrame()
        tab_ics.setObjectName("voxCard")
        i_lay = QVBoxLayout(tab_ics)
        i_lay.setContentsMargins(18, 16, 18, 16)
        i_lay.setSpacing(10)

        lbl_i_steps = QLabel(
            "Connect any published iCalendar (.ics) URL or local .ics file from Outlook, Google Calendar, "
            "or Apple Calendar, or synchronize directly with local Classic Outlook Desktop (MAPI)."
        )
        lbl_i_steps.setWordWrap(True)
        i_lay.addWidget(lbl_i_steps)

        ics_row = QHBoxLayout()
        ics_row.setSpacing(8)
        self.txt_ics_url = QLineEdit(str(getattr(self.controller.config, "calendar_ics_url", "") or ""))
        self.txt_ics_url.setPlaceholderText("Paste https://.../calendar.ics or webcal://... URL, or select file")
        self.btn_browse_ics = QPushButton("Browse .ics...")
        self.btn_browse_ics.setObjectName("secondaryBtn")
        self.btn_browse_ics.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_browse_ics.clicked.connect(self._browse_ics)
        ics_row.addWidget(self.txt_ics_url, 1)
        ics_row.addWidget(self.btn_browse_ics)
        i_lay.addLayout(ics_row)

        ics_act = QHBoxLayout()
        self.btn_ics_connect = QPushButton("Connect Calendar Feed (.ics)")
        self.btn_ics_connect.setObjectName("fathomBtn")
        self.btn_ics_connect.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ics_connect.clicked.connect(self._start_ics)
        ics_act.addWidget(self.btn_ics_connect)

        self.btn_mapi_connect = QPushButton("Auto-Detect Classic Outlook (MAPI)")
        self.btn_mapi_connect.setObjectName("secondaryBtn")
        self.btn_mapi_connect.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mapi_connect.clicked.connect(self._start_mapi)
        ics_act.addWidget(self.btn_mapi_connect)
        ics_act.addStretch(1)
        i_lay.addLayout(ics_act)

        i_lay.addStretch(1)
        self.stack.addWidget(tab_ics)

        # -------------------------------------------------------------
        # Live status banner
        # -------------------------------------------------------------
        self.status_banner = QFrame()
        self.status_banner.setObjectName("voxWellCard")
        self.status_banner.setVisible(False)
        sb_lay = QVBoxLayout(self.status_banner)
        sb_lay.setContentsMargins(14, 10, 14, 10)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        sb_lay.addWidget(self.lbl_status)
        root.addWidget(self.status_banner)

        footer = QHBoxLayout()
        self.btn_pick_calendars = QPushButton("Choose Calendars...")
        self.btn_pick_calendars.setObjectName("dawnBtn")
        self.btn_pick_calendars.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pick_calendars.setVisible(False)
        self.btn_pick_calendars.clicked.connect(self._open_calendar_picker_for_latest)
        footer.addWidget(self.btn_pick_calendars)

        footer.addStretch(1)
        self.btn_close = QPushButton("Close")
        self.btn_close.setObjectName("secondaryBtn")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.clicked.connect(self.reject)
        footer.addWidget(self.btn_close)
        root.addLayout(footer)

        self._picking_calendars: bool = False
        self._current_verification_uri: str = "https://microsoft.com/devicelogin"
        self._current_user_code: str = ""
        self._select_tab(0)

    def _select_tab(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._tab_buttons):
            btn.setChecked(i == idx)
            btn.setObjectName("dawnBtn" if i == idx else "secondaryBtn")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        for btn in (
            self.btn_ms_start,
            self.btn_ms_pkce,
            self.btn_google_start,
            self.btn_zoom_start,
            self.btn_ics_connect,
            self.btn_mapi_connect,
            self.btn_browse_ics,
        ):
            btn.setEnabled(not busy)

        self.btn_ms_cancel.setVisible(busy and self._cancel_event is not None)
        self.btn_google_cancel.setVisible(busy and self._cancel_event is not None)
        self.btn_zoom_cancel.setVisible(busy and self._cancel_event is not None)

        if message:
            self._show_status(message, kind="info")

    def _show_status(self, message: str, kind: str = "info") -> None:
        name = {"ok": "lumenBanner", "err": "dawnBanner"}.get(kind, "voxWellCard")
        self.status_banner.setObjectName(name)
        self.status_banner.style().unpolish(self.status_banner)
        self.status_banner.style().polish(self.status_banner)
        self.lbl_status.setText(message)
        self.status_banner.setVisible(True)

    def _cancel_pending(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        self.code_card.setVisible(False)
        self._set_busy(False, "Sign-in cancelled.")

    # --- Microsoft Graph Handlers ---
    def _start_ms_device_code(self) -> None:
        if not hasattr(self.controller, "connect_microsoft_calendar"):
            self._show_status("Calendar controller unavailable.", kind="err")
            return
        self._cancel_event = threading.Event()
        self._set_busy(True, "Requesting Microsoft sign-in code...")
        client_id = self.txt_ms_client_id.text().strip() or None
        tenant_id = self.txt_ms_tenant_id.text().strip() or "common"
        account_hint = self.txt_ms_account_hint.text().strip() or None

        self.controller.connect_microsoft_calendar(
            on_complete=lambda ok, msg: self._async_result.emit(bool(ok), str(msg)),
            on_device_code=lambda code, uri: self._device_code_received.emit(code, uri),
            client_id=client_id,
            tenant_id=tenant_id,
            account_hint=account_hint,
            use_device_code=True,
            cancel_event=self._cancel_event,
        )

    def _start_ms_browser_pkce(self) -> None:
        if not hasattr(self.controller, "connect_microsoft_calendar"):
            self._show_status("Calendar controller unavailable.", kind="err")
            return
        self._cancel_event = threading.Event()
        self._set_busy(True, "Opening Microsoft sign-in in browser...")
        client_id = self.txt_ms_client_id.text().strip() or None
        tenant_id = self.txt_ms_tenant_id.text().strip() or "common"
        account_hint = self.txt_ms_account_hint.text().strip() or None

        self.controller.connect_microsoft_calendar(
            on_complete=lambda ok, msg: self._async_result.emit(bool(ok), str(msg)),
            client_id=client_id,
            tenant_id=tenant_id,
            account_hint=account_hint,
            use_device_code=False,
            cancel_event=self._cancel_event,
        )

    def _on_device_code_received(self, user_code: str, verification_uri: str) -> None:
        self._current_user_code = user_code
        self._current_verification_uri = verification_uri
        self.lbl_user_code.setText(user_code)
        self.code_card.setVisible(True)
        self.lbl_poll_status.setText(f"Waiting for approval at {verification_uri}...")
        self._show_status(
            f"Code {user_code} generated. Copied to clipboard. Enter code at {verification_uri}.",
            kind="info",
        )
        self._copy_code_and_open_url()

    def _copy_code_and_open_url(self) -> None:
        if self._current_user_code:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(self._current_user_code)
        if self._current_verification_uri:
            try:
                webbrowser.open(self._current_verification_uri)
            except Exception as exc:
                logger.debug("Failed opening browser: %s", exc)

    # --- Google Calendar Handlers ---
    def _start_google_oauth(self) -> None:
        if not hasattr(self.controller, "connect_google_calendar"):
            self._show_status("Google calendar provider unavailable.", kind="err")
            return
        self._cancel_event = threading.Event()
        self._set_busy(True, "Opening Google sign-in in browser. Complete sign-in to approve VOX...")
        c_id = self.txt_google_client_id.text().strip() or None
        c_sec = self.txt_google_client_secret.text().strip() or None

        self.controller.connect_google_calendar(
            client_id=c_id,
            client_secret=c_sec,
            on_complete=lambda ok, msg: self._async_result.emit(bool(ok), str(msg)),
            cancel_event=self._cancel_event,
        )

    # --- Zoom Meetings Handlers ---
    def _start_zoom_oauth(self) -> None:
        if not hasattr(self.controller, "connect_zoom_calendar"):
            self._show_status("Zoom provider unavailable.", kind="err")
            return
        self._cancel_event = threading.Event()
        self._set_busy(True, "Opening Zoom authorization in browser. Complete authorization to approve VOX...")
        c_id = self.txt_zoom_client_id.text().strip() or None
        c_sec = self.txt_zoom_client_secret.text().strip() or None

        self.controller.connect_zoom_calendar(
            client_id=c_id,
            client_secret=c_sec,
            on_complete=lambda ok, msg: self._async_result.emit(bool(ok), str(msg)),
            cancel_event=self._cancel_event,
        )

    # --- ICS / MAPI Handlers ---
    def _browse_ics(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Outlook / Calendar .ics File",
            "",
            "iCalendar Files (*.ics);;All Files (*.*)",
        )
        if path:
            self.txt_ics_url.setText(path)

    def _start_ics(self) -> None:
        url = self.txt_ics_url.text().strip()
        if not url:
            self._show_status("Paste an Outlook .ics link or browse to a local .ics file first.", kind="err")
            return
        if not hasattr(self.controller, "connect_ics_calendar"):
            self._show_status("ICS calendar provider unavailable.", kind="err")
            return
        self._cancel_event = None
        self._set_busy(True, "Validating Outlook ICS calendar feed...")
        self.controller.connect_ics_calendar(
            url,
            on_complete=lambda ok, msg: self._async_result.emit(bool(ok), str(msg)),
        )

    def _start_mapi(self) -> None:
        if not hasattr(self.controller, "connect_local_outlook_calendar"):
            self._show_status("Local Outlook provider unavailable.", kind="err")
            return
        self._cancel_event = None
        self._set_busy(True, "Checking for local Classic Outlook MAPI profile...")
        self.controller.connect_local_outlook_calendar(
            on_complete=lambda ok, msg: self._async_result.emit(bool(ok), str(msg)),
        )

    def _on_worker_finished(self, ok: bool, msg: str) -> None:
        self._cancel_event = None
        self._set_busy(False)
        self.code_card.setVisible(False)
        if ok:
            self._connected_msg = msg
            self._show_status(
                f"{msg} Syncing upcoming events automatically.",
                kind="ok",
            )
            current_tab = self.stack.currentIndex() if hasattr(self, "stack") else 0
            if current_tab in (0, 1) and hasattr(self.controller, "list_connected_calendar_accounts"):
                self.btn_pick_calendars.setVisible(True)
            self.connected.emit(msg)
            QTimer.singleShot(1100, self._maybe_auto_accept)
        else:
            _code, friendly = sanitize_oauth_error_for_user(msg)
            self._show_status(friendly, kind="err")

    def _maybe_auto_accept(self) -> None:
        if not getattr(self, "_picking_calendars", False):
            self.accept()

    def _open_calendar_picker_for_latest(self) -> None:
        self._picking_calendars = True
        try:
            accounts = self.controller.list_connected_calendar_accounts() if hasattr(self.controller, "list_connected_calendar_accounts") else []
            target = None
            tab = self.stack.currentIndex()
            want_type = "ms_graph" if tab == 0 else ("google" if tab == 1 else "")
            for acc in reversed(accounts):
                if not want_type or acc.get("provider_type") == want_type:
                    target = acc
                    break
            if target is not None:
                from voxlocal.ui.calendar_page import CalendarSelectionDialog
                dlg = CalendarSelectionDialog(self.controller, target, theme=self._theme, parent=self)
                dlg.exec()
        finally:
            self._picking_calendars = False
            self.accept()

    def reject(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        super().reject()

    def apply_theme(self, theme_name: str) -> None:
        self._theme = "dark" if (theme_name or "").lower() == "dark" else "light"
        t = get_theme_tokens(self._theme)
        self.setStyleSheet(
            get_dialog_stylesheet(self._theme)
            + f"""
            QLabel#onbSub {{
                color: {t['text_secondary']};
                font-size: 13px;
            }}
            QLineEdit {{
                min-height: 22px;
                padding: 6px 10px;
            }}
            """
        )

"""VOX Smart 2026 Pro Paywall & Upgrade Dialog."""

from __future__ import annotations

import datetime
import webbrowser
from typing import Any, Optional

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from voxlocal.core.logger import get_logger
from voxlocal.ui.theme import (
    create_app_pixmap,
    get_dialog_stylesheet,
    get_theme_tokens,
    get_ui_font,
)

logger = get_logger("ui.pro_upgrade_dialog")

RAZORPAY_MONTHLY_LINK = "https://rzp.io/rzp/ugv7JBF2"
RAZORPAY_YEARLY_LINK = "https://rzp.io/rzp/fycKln1w"


class VoxProUpgradeDialog(QDialog):
    """
    Modern 2026-style Paywall & Pro Feature Upgrade Dialog for VOX.
    Features:
    - Context-aware trigger reasons (quota exhaustion, premium exports, Ask VOX queries).
    - Monthly vs. Annual plan cards with 33% discount highlighting.
    - Direct 1-click Razorpay checkout redirection.
    - Instant in-app Payment ID / License Key activation with DPAPI vault persistence.
    """

    upgraded = pyqtSignal(dict)  # Emitted when user upgrades / activates Pro

    def __init__(
        self,
        controller: Any,
        reason: str = "",
        theme: str = "light",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.reason_text = reason or "Unlock unlimited speech-to-text, meeting intelligence, and cross-meeting memory."
        self.current_theme = (theme or "light").lower()
        self.tokens = get_theme_tokens(self.current_theme)

        self.setWindowTitle("VOX Pro — Unlimited Intelligence")
        self.setFixedSize(560, 680)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._build_ui()
        self.setStyleSheet(get_dialog_stylesheet(self.current_theme))

        if hasattr(self.controller, "entitlement_changed"):
            try:
                self.controller.entitlement_changed.connect(self._on_entitlement_changed)
            except Exception:
                pass

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(16)

        # Header with Logo & Eyebrow Badge
        header_row = QHBoxLayout()
        header_row.setSpacing(14)

        logo_lbl = QLabel()
        logo_pix = create_app_pixmap(state="idle", size=54, theme=self.current_theme)
        logo_lbl.setPixmap(logo_pix)
        logo_lbl.setFixedSize(54, 54)
        header_row.addWidget(logo_lbl)

        header_text_lay = QVBoxLayout()
        header_text_lay.setSpacing(2)

        badge_row = QHBoxLayout()
        badge_lbl = QLabel("VOX PRO · UNLIMITED INTELLIGENCE")
        badge_lbl.setStyleSheet(
            f"background: {self.tokens['accent_dawn']}; color: {self.tokens['accent_dawn_text']}; "
            f"font-size: 11px; font-weight: 700; letter-spacing: 0.8px; padding: 2px 8px; border-radius: 999px;"
        )
        badge_row.addWidget(badge_lbl)
        badge_row.addStretch()
        header_text_lay.addLayout(badge_row)

        headline_lbl = QLabel("Upgrade to VOX Pro")
        headline_lbl.setFont(get_ui_font(size_px=22, weight=700))
        headline_lbl.setStyleSheet(f"color: {self.tokens['text_primary']};")
        header_text_lay.addWidget(headline_lbl)

        header_row.addLayout(header_text_lay)
        header_row.addStretch()
        layout.addLayout(header_row)

        # Trigger Context / Notification Box
        if self.reason_text:
            reason_box = QFrame()
            reason_box.setObjectName("voxWellCard")
            reason_lay = QHBoxLayout(reason_box)
            reason_lay.setContentsMargins(12, 10, 12, 10)
            reason_lay.setSpacing(10)

            sparkle_lbl = QLabel("✦")
            sparkle_lbl.setFont(get_ui_font(size_px=14, weight=700))
            sparkle_lbl.setStyleSheet(f"color: {self.tokens['accent_purple']};")
            reason_lay.addWidget(sparkle_lbl)

            self.reason_lbl = QLabel(self.reason_text)
            self.reason_lbl.setFont(get_ui_font(size_px=12, weight=550))
            self.reason_lbl.setStyleSheet(f"color: {self.tokens['text_secondary']};")
            self.reason_lbl.setWordWrap(True)
            reason_lay.addWidget(self.reason_lbl, 1)

            layout.addWidget(reason_box)

        # Feature Highlights Matrix
        feats_frame = QFrame()
        feats_frame.setObjectName("voxCard")
        feats_lay = QVBoxLayout(feats_frame)
        feats_lay.setContentsMargins(16, 14, 16, 14)
        feats_lay.setSpacing(8)

        features = [
            ("⚡", "Unlimited Voice Typing", "No 3,000 weekly word cap — type continuously at 220+ WPM"),
            ("🎙️", "Unlimited Meeting Studio", "Dual-track mic + system audio, 1080p video sync, no duration limits"),
            ("🧠", "Unlimited Cognitive Ask VOX", "Query across all past meetings with grounded citations"),
            ("📑", "Executive MoM & Deliverable Exports", "Export in Markdown, PDF, HTML, JSON, and clean action items"),
            ("🔒", "100% Local PC Privacy", "Zero audio uploads to cloud servers — runs offline on your machine"),
        ]

        for icon, title, desc in features:
            f_row = QHBoxLayout()
            f_row.setSpacing(10)

            ic_lbl = QLabel(icon)
            ic_lbl.setFixedWidth(20)
            f_row.addWidget(ic_lbl)

            tx_lay = QVBoxLayout()
            tx_lay.setSpacing(1)
            t_lbl = QLabel(title)
            t_lbl.setFont(get_ui_font(size_px=12, weight=700))
            t_lbl.setStyleSheet(f"color: {self.tokens['text_primary']};")
            tx_lay.addWidget(t_lbl)

            d_lbl = QLabel(desc)
            d_lbl.setFont(get_ui_font(size_px=11, weight=400))
            d_lbl.setStyleSheet(f"color: {self.tokens['text_muted']};")
            tx_lay.addWidget(d_lbl)

            f_row.addLayout(tx_lay, 1)
            feats_lay.addLayout(f_row)

        layout.addWidget(feats_frame)

        # Plan Cards Side-by-Side (Monthly & Annual)
        plans_row = QHBoxLayout()
        plans_row.setSpacing(12)

        # Monthly Card
        self.card_monthly = QFrame()
        self.card_monthly.setObjectName("voxWellCard")
        m_lay = QVBoxLayout(self.card_monthly)
        m_lay.setContentsMargins(14, 14, 14, 14)
        m_lay.setSpacing(6)

        m_head = QLabel("VOX Pro Monthly")
        m_head.setFont(get_ui_font(size_px=13, weight=700))
        m_lay.addWidget(m_head)

        m_price = QLabel("₹199")
        m_price.setFont(get_ui_font(size_px=22, weight=800))
        m_price.setStyleSheet(f"color: {self.tokens['accent_fathom']};")
        m_sub = QLabel("per month, cancel anytime")
        m_sub.setFont(get_ui_font(size_px=11, weight=500))
        m_sub.setStyleSheet(f"color: {self.tokens['text_muted']};")
        m_lay.addWidget(m_price)
        m_lay.addWidget(m_sub)

        self.btn_pay_monthly = QPushButton("Upgrade Monthly →")
        self.btn_pay_monthly.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pay_monthly.setObjectName("dawnBtn")
        self.btn_pay_monthly.clicked.connect(self._open_monthly_checkout)
        m_lay.addWidget(self.btn_pay_monthly)
        plans_row.addWidget(self.card_monthly)

        # Annual Card (Featured)
        self.card_annual = QFrame()
        self.card_annual.setObjectName("voxCard")
        self.card_annual.setStyleSheet(f"border: 2px solid {self.tokens['accent_fathom']};")
        a_lay = QVBoxLayout(self.card_annual)
        a_lay.setContentsMargins(14, 14, 14, 14)
        a_lay.setSpacing(6)

        a_top = QHBoxLayout()
        a_head = QLabel("VOX Pro Annual")
        a_head.setFont(get_ui_font(size_px=13, weight=700))
        a_top.addWidget(a_head)
        a_badge = QLabel("SAVE 33%")
        a_badge.setStyleSheet(
            "background: #10B981; color: #FFFFFF; font-size: 10px; font-weight: 800; "
            "padding: 2px 6px; border-radius: 999px;"
        )
        a_top.addWidget(a_badge)
        a_lay.addLayout(a_top)

        a_price = QLabel("₹1,599")
        a_price.setFont(get_ui_font(size_px=22, weight=800))
        a_price.setStyleSheet(f"color: {self.tokens['accent_fathom']};")
        a_sub = QLabel("per year (~₹133/month)")
        a_sub.setFont(get_ui_font(size_px=11, weight=500))
        a_sub.setStyleSheet(f"color: {self.tokens['text_muted']};")
        a_lay.addWidget(a_price)
        a_lay.addWidget(a_sub)

        self.btn_pay_annual = QPushButton("Upgrade Annual (Best Value) →")
        self.btn_pay_annual.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pay_annual.setObjectName("primaryBtn")
        self.btn_pay_annual.clicked.connect(self._open_yearly_checkout)
        a_lay.addWidget(self.btn_pay_annual)
        plans_row.addWidget(self.card_annual)

        layout.addLayout(plans_row)

        # Automatic Cloud Activation Card (Manual backdoor completely eliminated)
        sync_box = QFrame()
        sync_box.setObjectName("voxWellCard")
        sync_lay = QVBoxLayout(sync_box)
        sync_lay.setContentsMargins(14, 12, 14, 12)
        sync_lay.setSpacing(8)

        sync_title_row = QHBoxLayout()
        sync_title_row.setSpacing(8)
        sync_icon = QLabel("⚡")
        sync_title_row.addWidget(sync_icon)

        sync_title = QLabel("Automatic Cloud Activation")
        sync_title.setFont(get_ui_font(size_px=12, weight=700))
        sync_title.setStyleSheet(f"color: {self.tokens['text_primary']};")
        sync_title_row.addWidget(sync_title)
        sync_title_row.addStretch()
        sync_lay.addLayout(sync_title_row)

        sync_desc = QLabel(
            "After completing payment in your browser, VOX automatically activates your Pro subscription. "
            "If you already completed checkout, click Refresh to synchronize immediately."
        )
        sync_desc.setFont(get_ui_font(size_px=11, weight=400))
        sync_desc.setStyleSheet(f"color: {self.tokens['text_secondary']};")
        sync_desc.setWordWrap(True)
        sync_lay.addWidget(sync_desc)

        sync_action_row = QHBoxLayout()
        sync_action_row.setSpacing(10)

        self.btn_sync = QPushButton("🔄 Refresh / Check Status")
        self.btn_sync.setObjectName("secondaryBtn")
        self.btn_sync.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sync.clicked.connect(self._on_sync_clicked)
        sync_action_row.addWidget(self.btn_sync)
        sync_action_row.addStretch()

        sync_lay.addLayout(sync_action_row)

        self.lbl_act_status = QLabel("")
        self.lbl_act_status.setFont(get_ui_font(size_px=11, weight=600))
        self.lbl_act_status.setVisible(False)
        self.lbl_act_status.setWordWrap(True)
        sync_lay.addWidget(self.lbl_act_status)

        layout.addWidget(sync_box)

        # Footer Actions (Maybe Later)
        footer_row = QHBoxLayout()
        footer_row.addStretch()

        self.btn_close = QPushButton("Maybe Later")
        self.btn_close.setObjectName("ghostBtn")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.clicked.connect(self.reject)
        footer_row.addWidget(self.btn_close)

        footer_row.addStretch()
        layout.addLayout(footer_row)

    def _open_monthly_checkout(self) -> None:
        logger.info("Opening Razorpay Monthly link: %s", RAZORPAY_MONTHLY_LINK)
        try:
            QDesktopServices.openUrl(QUrl(RAZORPAY_MONTHLY_LINK))
        except Exception:
            pass
        webbrowser.open(RAZORPAY_MONTHLY_LINK)

    def _open_yearly_checkout(self) -> None:
        logger.info("Opening Razorpay Yearly link: %s", RAZORPAY_YEARLY_LINK)
        try:
            QDesktopServices.openUrl(QUrl(RAZORPAY_YEARLY_LINK))
        except Exception:
            pass
        webbrowser.open(RAZORPAY_YEARLY_LINK)

    def _on_sync_clicked(self) -> None:
        """Trigger cloud entitlement synchronization and verify active subscription."""
        # 1. Immediate check if controller is already Pro
        ent = getattr(self.controller, "entitlement", None)
        if ent and hasattr(ent, "get_state"):
            state = ent.get_state()
            if getattr(state, "is_pro", False):
                self._show_status("🎉 VOX Pro is active! Enjoy unlimited intelligence.", is_error=False)
                eff = ent.get_effective_entitlement() if hasattr(ent, "get_effective_entitlement") else {"is_pro": True}
                if not isinstance(eff, dict):
                    eff = {"is_pro": True}
                self.upgraded.emit(eff)
                self.btn_sync.setEnabled(False)
                return

        # 2. Trigger cloud sync via controller
        if hasattr(self.controller, "sync_entitlements_with_cloud"):
            self._show_status("Synchronizing with VOX Cloud... Please wait.", is_error=False)
            try:
                self.controller.sync_entitlements_with_cloud()
            except Exception as exc:
                logger.warning("Cloud sync error: %s", exc)
                self._show_status(f"Sync error: {exc}", is_error=True)
                return

            # Re-check state if updated synchronously
            if ent and hasattr(ent, "get_state") and getattr(ent.get_state(), "is_pro", False):
                self._show_status("🎉 VOX Pro is active! Enjoy unlimited intelligence.", is_error=False)
                eff = ent.get_effective_entitlement() if hasattr(ent, "get_effective_entitlement") else {"is_pro": True}
                if not isinstance(eff, dict):
                    eff = {"is_pro": True}
                self.upgraded.emit(eff)
                self.btn_sync.setEnabled(False)
            else:
                self._show_status(
                    "Checking cloud status... If checkout just finished, please wait a few seconds and click Refresh again.",
                    is_error=False,
                )
        else:
            self._show_status("Synchronization service unavailable.", is_error=True)

    def _on_entitlement_changed(self, ent_dict: dict) -> None:
        """Handle incoming entitlement update from background cloud sync."""
        if ent_dict and ent_dict.get("is_pro"):
            self._show_status("🎉 VOX Pro is active! Enjoy unlimited intelligence.", is_error=False)
            self.upgraded.emit(ent_dict)
            if hasattr(self, "btn_sync"):
                self.btn_sync.setEnabled(False)

    def _show_status(self, text: str, is_error: bool = False) -> None:
        self.lbl_act_status.setText(text)
        color = self.tokens["accent_crimson"] if is_error else self.tokens["accent_emerald"]
        self.lbl_act_status.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")
        self.lbl_act_status.setVisible(True)

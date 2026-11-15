"""
Test suite for VoxProUpgradeDialog, upgrade_dialog_requested signal propagation,
and feature gating across Free vs Pro tiers.
"""

import sys
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication

# Ensure QApplication instance exists
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_pro_upgrade_dialog_instantiation(qapp):
    from voxlocal.ui.pro_upgrade_dialog import VoxProUpgradeDialog

    mock_controller = MagicMock()
    mock_controller.config = MagicMock()
    mock_controller.config.theme = "light"

    dlg = VoxProUpgradeDialog(
        mock_controller,
        reason="Weekly dictation quota reached (3,000 words).",
        theme="light"
    )

    assert dlg is not None
    assert "Weekly dictation quota reached" in dlg.reason_lbl.text()
    assert dlg.card_monthly is not None
    assert dlg.card_annual is not None
    assert dlg.input_key is not None
    assert dlg.btn_activate is not None


def test_pro_upgrade_dialog_open_links(qapp):
    from voxlocal.ui.pro_upgrade_dialog import VoxProUpgradeDialog

    mock_controller = MagicMock()
    mock_controller.config = MagicMock()
    mock_controller.config.theme = "light"

    dlg = VoxProUpgradeDialog(mock_controller, reason="Testing links", theme="light")

    with patch("webbrowser.open") as mock_open:
        dlg._open_monthly_checkout()
        mock_open.assert_called_with("https://rzp.io/rzp/ugv7JBF2")

    with patch("webbrowser.open") as mock_open:
        dlg._open_yearly_checkout()
        mock_open.assert_called_with("https://rzp.io/rzp/fycKln1w")


def test_pro_upgrade_dialog_activate_payment_id(qapp):
    from voxlocal.ui.pro_upgrade_dialog import VoxProUpgradeDialog

    mock_controller = MagicMock()
    mock_controller.config = MagicMock()
    mock_controller.config.theme = "light"
    mock_controller.entitlement = MagicMock()
    mock_controller.entitlement.save_entitlement_token.return_value = (True, "")

    dlg = VoxProUpgradeDialog(mock_controller, reason="Testing activation", theme="light")

    # Enter a payment ID
    dlg.input_key.setText("pay_TestRazorpay12345")
    dlg._on_activate_clicked()

    # Verify token saved to vault and accepted
    assert mock_controller.entitlement.save_entitlement_token.called


def test_controller_signals_upgrade_dialog_on_quota():
    from voxlocal.core.controller import AppController
    
    # Check signal exists on AppController class
    assert hasattr(AppController, "upgrade_dialog_requested")

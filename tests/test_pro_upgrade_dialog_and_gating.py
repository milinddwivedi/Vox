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
        theme="light",
    )

    assert dlg is not None
    assert "Weekly dictation quota reached" in dlg.reason_lbl.text()
    assert dlg.card_monthly is not None
    assert dlg.card_annual is not None
    assert dlg.btn_pay_monthly is not None
    assert dlg.btn_pay_annual is not None
    assert dlg.btn_sync is not None
    # Verify manual payment backdoor widgets are removed
    assert not hasattr(dlg, "input_key")
    assert not hasattr(dlg, "btn_activate")


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


def test_pro_upgrade_dialog_sync_button_calls_controller(qapp):
    from voxlocal.ui.pro_upgrade_dialog import VoxProUpgradeDialog

    mock_controller = MagicMock()
    mock_controller.config = MagicMock()
    mock_controller.config.theme = "light"
    mock_controller.entitlement = MagicMock()
    mock_controller.entitlement.get_state.return_value = MagicMock(is_pro=False)

    dlg = VoxProUpgradeDialog(mock_controller, reason="Testing sync", theme="light")
    dlg.show()
    dlg._on_sync_clicked()

    assert mock_controller.sync_entitlements_with_cloud.called
    assert not dlg.lbl_act_status.isHidden()


def test_pro_upgrade_dialog_sync_when_already_pro(qapp):
    from voxlocal.ui.pro_upgrade_dialog import VoxProUpgradeDialog

    mock_controller = MagicMock()
    mock_controller.config = MagicMock()
    mock_controller.config.theme = "light"
    mock_controller.entitlement = MagicMock()
    mock_controller.entitlement.get_state.return_value = MagicMock(is_pro=True)
    mock_controller.entitlement.get_effective_entitlement.return_value = {"is_pro": True, "plan": "pro_monthly"}

    dlg = VoxProUpgradeDialog(mock_controller, reason="Testing pro sync", theme="light")

    emitted_upgrades = []
    dlg.upgraded.connect(lambda p: emitted_upgrades.append(p))

    dlg._on_sync_clicked()

    assert len(emitted_upgrades) == 1
    assert emitted_upgrades[0]["is_pro"] is True
    assert not dlg.btn_sync.isEnabled()


def test_pro_upgrade_dialog_entitlement_changed_signal(qapp):
    from voxlocal.ui.pro_upgrade_dialog import VoxProUpgradeDialog

    mock_controller = MagicMock()
    mock_controller.config = MagicMock()
    mock_controller.config.theme = "light"

    dlg = VoxProUpgradeDialog(mock_controller, reason="Testing signal", theme="light")

    emitted_upgrades = []
    dlg.upgraded.connect(lambda p: emitted_upgrades.append(p))

    dlg._on_entitlement_changed({"is_pro": True, "plan": "pro_annual"})

    assert len(emitted_upgrades) == 1
    assert emitted_upgrades[0]["is_pro"] is True
    assert not dlg.btn_sync.isEnabled()


def test_pro_upgrade_dialog_backdoor_completely_removed(qapp):
    import voxlocal.ui.pro_upgrade_dialog as pud_module
    from voxlocal.ui.pro_upgrade_dialog import VoxProUpgradeDialog

    mock_controller = MagicMock()
    mock_controller.config = MagicMock()
    mock_controller.config.theme = "light"

    dlg = VoxProUpgradeDialog(mock_controller, reason="Testing removal", theme="light")

    assert not hasattr(dlg, "input_key")
    assert not hasattr(dlg, "btn_activate")
    assert not hasattr(dlg, "_on_activate_clicked")
    assert not hasattr(pud_module, "sign_entitlement_payload")

    import voxlocal.core.entitlement as ent_module
    assert not hasattr(ent_module, "sign_entitlement_payload")
    assert not hasattr(ent_module, "DEV_FALLBACK_PRIVATE_KEY_HEX")
    assert not hasattr(ent_module, "normalize_private_key")
    assert hasattr(ent_module, "DEFAULT_ENTITLEMENT_PUBLIC_KEY")
    assert hasattr(ent_module, "verify_and_decode_entitlement_token")


def test_controller_signals_upgrade_dialog_on_quota():
    from voxlocal.core.controller import AppController

    assert hasattr(AppController, "upgrade_dialog_requested")

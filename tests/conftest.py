import sys
from PyQt6.QtWidgets import QApplication

# Initialize native Windows platform QApplication before offscreen QPA env var
# is set by E2E test suites, ensuring real Win32 HWNDs are valid for focus invariance tests.
_NATIVE_APP = QApplication.instance()
if _NATIVE_APP is None:
    _NATIVE_APP = QApplication(["vox_test"])

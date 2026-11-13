import os
import sys
import time
from pathlib import Path

# Add src and tests to path
root_dir = Path(".").resolve()
sys.path.insert(0, str(root_dir / "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication

app = QApplication.instance()
if not app:
    app = QApplication([])

clipboard = app.clipboard()

print("Testing rapid clipboard set and get without processEvents...")
failures_no_pe = 0
for i in range(100):
    val = f"Token_{i}_{time.time()}"
    clipboard.setText(val)
    got = clipboard.text()
    if got != val:
        failures_no_pe += 1
print(f"Results without processEvents: {failures_no_pe}/100 mismatches")

print("\nTesting rapid clipboard set and get WITH processEvents...")
failures_with_pe = 0
for i in range(100):
    val = f"Token_PE_{i}_{time.time()}"
    clipboard.setText(val)
    app.processEvents()
    got = clipboard.text()
    if got != val:
        failures_with_pe += 1
print(f"Results WITH processEvents: {failures_with_pe}/100 mismatches")

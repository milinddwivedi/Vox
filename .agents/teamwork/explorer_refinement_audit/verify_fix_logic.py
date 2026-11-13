import os
import sys
import time
from pathlib import Path

root_dir = Path(".").resolve()
sys.path.insert(0, str(root_dir / "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication

app = QApplication.instance()
if not app:
    app = QApplication([])

def safe_get_clipboard_text(expected_substring: str = None, timeout: float = 1.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        cb = app.clipboard()
        if cb:
            txt = cb.text()
            if expected_substring is None or expected_substring in txt:
                return txt
        time.sleep(0.02)
    return app.clipboard().text() if app.clipboard() else ""

# Simulate consecutive test execution
print("Simulating Test 5 (Meeting Comparison)...")
cb = app.clipboard()
cb.setText("*⚖️ VOX Meeting Comparison: Everlane vs Deployment*")
app.processEvents()
t5_text = safe_get_clipboard_text("Meeting Comparison")
assert "Meeting Comparison" in t5_text, f"Failed T5: {t5_text!r}"
print("  Test 5 verified: 'Meeting Comparison' found.")

# Simulate tearDown
app.processEvents()

# Simulate Test 6 (Pre-Meeting Briefing)...
print("Simulating Test 6 (Pre-Meeting Briefing)...")
cb.setText("*🎯 VOX Pre-Meeting Briefing: Everlane*")
app.processEvents()
t6_text = safe_get_clipboard_text("Pre-Meeting Briefing")
assert "Pre-Meeting Briefing" in t6_text, f"Failed T6: {t6_text!r}"
print("  Test 6 verified: 'Pre-Meeting Briefing' found.")
print("\nSimulation PASSED cleanly!")

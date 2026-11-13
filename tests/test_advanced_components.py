import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
"""Unit tests for the new UI components and main window integration."""
import unittest
import os
import sys
from pathlib import Path

# Add src to path
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from voxlocal.ui.components import (
    VoxKineticTubesReactor,
    VoxPrintedArtifactCard,
    VoxHorizontalCardRail,
    VoxExplodedCanvas,
    VoxExplodedArchitectureDialog,
)

app = QApplication.instance() or QApplication([])

class TestAdvancedComponents(unittest.TestCase):
    def test_kinetic_tubes_reactor(self):
        tubes = VoxKineticTubesReactor(height=50)
        self.assertIsNotNone(tubes)
        tubes.set_audio_level(0.75)
        self.assertAlmostEqual(tubes._audio_level, 0.75)
        # Test palette cycling
        p0 = tubes._palette_idx
        idx = tubes.cycle_palette()
        self.assertEqual(idx, (p0 + 1) % len(tubes._palettes))

    def test_printed_artifact_card(self):
        card = VoxPrintedArtifactCard()
        card.set_data(
            meeting_id=42,
            title="Q3 Strategy Review",
            metadata="2026-10-29 • 45m 12s • Microsoft Teams",
            summary="Approved new cloud-free privacy architecture.\nAction items assigned to Alice and Bob.",
            platform="Microsoft Teams",
        )
        self.assertEqual(card._lot_number, "LOT-VOX-M0042")
        self.assertEqual(card._title, "Q3 Strategy Review")
        self.assertIn("Alice", card._summary)

    def test_horizontal_card_rail(self):
        rail = VoxHorizontalCardRail()
        sample_chapters = [
            {"time_sec": 0.0, "title": "Intro", "speaker": "Alice", "summary": "Welcome and agenda"},
            {"time_sec": 120.0, "title": "Architecture", "speaker": "Bob", "summary": "Reviewing 4-layer stack"},
            {"time_sec": 300.0, "title": "Action Items", "speaker": "Alice", "summary": "Final wrapup"},
        ]
        rail.set_chapters(sample_chapters)
        self.assertEqual(len(rail._chapters), 3)
        # Test playback scrub
        rail.set_playback_seconds(150.0)
        self.assertEqual(rail._current_chapter_idx, 1)

    def test_exploded_architecture_dialog(self):
        canvas = VoxExplodedCanvas()
        canvas.set_explode(0.8)
        self.assertAlmostEqual(canvas._explode, 0.8)
        
        dlg = VoxExplodedArchitectureDialog()
        self.assertIsNotNone(dlg)
        self.assertIsNotNone(dlg.canvas)
        dlg.slider.setValue(40)
        self.assertAlmostEqual(dlg._explode_amount, 0.4)

if __name__ == "__main__":
    unittest.main()

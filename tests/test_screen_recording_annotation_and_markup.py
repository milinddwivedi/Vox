from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
"""
Comprehensive Automated Tests for VOX Core Feature 2:
Screen Recording, Live Annotation, Physical Pixel Redaction,
Tutorial Step Numbering, and Image Markup Studio.
"""


import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import av
import numpy as np
from PyQt6.QtCore import QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

from voxlocal.annotation.engine import AnnotationEngine, AnnotationItem
from voxlocal.win32.clipboard import (
    copy_file_to_clipboard,
    copy_image_to_clipboard,
    copy_text_to_clipboard,
)

# Ensure QApplication exists for GUI component testing
app = QApplication.instance() or QApplication([])


class TestAnnotationDataModelAndEngine(unittest.TestCase):
    """Validates vector data structures, serialization, and undo/redo stacks."""

    def test_item_serialization_roundtrip(self) -> None:
        """Every annotation type must serialize to dict and reconstruct accurately."""
        item = AnnotationItem(
            kind="arrow",
            points=[(10.5, 20.5), (100.0, 200.0)],
            color="#38BDF8",
            stroke_width=6,
            opacity=0.85,
            step_number=3,
            text="Callout Arrow",
        )
        d = item.to_dict()
        reconstructed = AnnotationItem.from_dict(d)

        self.assertEqual(reconstructed.kind, "arrow")
        self.assertEqual(len(reconstructed.points), 2)
        self.assertAlmostEqual(reconstructed.points[0][0], 10.5)
        self.assertEqual(reconstructed.color, "#38BDF8")
        self.assertEqual(reconstructed.stroke_width, 6)
        self.assertAlmostEqual(reconstructed.opacity, 0.85)
        self.assertEqual(reconstructed.text, "Callout Arrow")
        self.assertEqual(reconstructed.step_number, 3)

    def test_undo_redo_history_stack(self) -> None:
        """Undo and Redo must strictly preserve command history and states."""
        engine = AnnotationEngine()
        self.assertFalse(engine.can_undo())
        self.assertFalse(engine.can_redo())

        item1 = AnnotationItem(kind="pen", points=[(0, 0), (10, 10)])
        item2 = AnnotationItem(kind="rect", x=10, y=10, w=50, h=50)

        engine.add_item(item1)
        self.assertEqual(engine.items_count, 1)
        self.assertTrue(engine.can_undo())
        self.assertFalse(engine.can_redo())

        engine.add_item(item2)
        self.assertEqual(engine.items_count, 2)

        # Undo item 2
        ok = engine.undo()
        self.assertTrue(ok)
        self.assertEqual(engine.items_count, 1)
        self.assertTrue(engine.can_redo())

        # Undo item 1
        ok = engine.undo()
        self.assertTrue(ok)
        self.assertEqual(engine.items_count, 0)
        self.assertFalse(engine.can_undo())
        self.assertTrue(engine.can_redo())

        # Redo item 1
        ok = engine.redo()
        self.assertTrue(ok)
        self.assertEqual(engine.items_count, 1)

        # Redo item 2
        ok = engine.redo()
        self.assertTrue(ok)
        self.assertEqual(engine.items_count, 2)

    def test_auto_incrementing_step_badges(self) -> None:
        """Tutorial step badges must automatically increment 1, 2, 3... and sync with history."""
        engine = AnnotationEngine()
        self.assertEqual(engine.get_next_step_number(), 1)

        s1 = AnnotationItem(kind="step", x=100, y=100, step_number=engine.get_next_step_number())
        engine.add_item(s1)
        self.assertEqual(engine.get_next_step_number(), 2)

        s2 = AnnotationItem(kind="step", x=200, y=200, step_number=engine.get_next_step_number())
        engine.add_item(s2)
        self.assertEqual(engine.get_next_step_number(), 3)

        # Undo s2
        engine.undo()
        self.assertEqual(engine.get_next_step_number(), 2)

        # Redo s2
        engine.redo()
        self.assertEqual(engine.get_next_step_number(), 3)

        # Clear resets step counter to 1
        engine.clear()
        self.assertEqual(engine.get_next_step_number(), 1)
        self.assertEqual(engine.items_count, 0)

    def test_thread_safe_concurrent_snapshots(self) -> None:
        """Engine must handle high-concurrency UI draws while background worker takes snapshots."""
        engine = AnnotationEngine()
        errors = []

        def worker_ui():
            try:
                for i in range(100):
                    engine.add_item(AnnotationItem(kind="pen", points=[(i, i), (i + 1, i + 1)]))
                    if i % 5 == 0:
                        engine.undo()
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def worker_video():
            try:
                buf = np.zeros((200, 200, 4), dtype=np.uint8)
                for _ in range(100):
                    engine.render_to_bgra(buf)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=worker_ui)
        t2 = threading.Thread(target=worker_video)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(errors), 0, f"Thread concurrency errors encountered: {errors}")


class TestPhysicalBlurAndRedaction(unittest.TestCase):
    """Validates that blur/redaction physically destroys pixel data for corporate privacy."""

    def test_physical_pixelation_alters_buffer(self) -> None:
        """Pixelation downsampling must physically obliterate fine text/numbers underneath."""
        # Create an image buffer with sharp high-frequency text-like alternating pixels
        buf = np.zeros((100, 100, 4), dtype=np.uint8)
        buf[30:70, 30:70, :] = 255  # bright square
        # Introduce a high frequency checkerboard pattern simulating small text
        for r in range(40, 60):
            for c in range(40, 60):
                if (r + c) % 2 == 0:
                    buf[r, c, :] = 0

        original_sample = buf[40:50, 40:50, :].copy()

        engine = AnnotationEngine()
        # Add a blur redaction box covering the patterned region
        blur_item = AnnotationItem(kind="blur", x=35, y=35, w=30, h=30)
        engine.add_item(blur_item)

        # Composite onto buffer
        engine.render_to_bgra(buf)
        redacted_sample = buf[40:50, 40:50, :]

        # High-frequency checkerboard pattern must be gone (flattened into blocks)
        self.assertFalse(np.array_equal(original_sample, redacted_sample))
        # Pixels outside the blur box must remain intact
        self.assertEqual(buf[10, 10, 0], 0)

    def test_render_to_qimage_composite(self) -> None:
        """render_to_qimage must leave original base image intact and produce marked composite."""
        base = QImage(200, 200, QImage.Format.Format_ARGB32)
        base.fill(QColor(255, 255, 255))

        engine = AnnotationEngine()
        engine.add_item(AnnotationItem(kind="rect", x=20, y=20, w=60, h=60, color="#EF4444", stroke_width=4))
        engine.add_item(AnnotationItem(kind="step", x=150, y=150, step_number=1, color="#10B981"))

        composite = engine.render_to_qimage(base)

        # Base image must still be pure white
        self.assertEqual(base.pixelColor(20, 20), QColor(255, 255, 255))

        # Composite image must have the red rectangle drawn
        self.assertNotEqual(composite.pixelColor(20, 20), QColor(255, 255, 255))
        self.assertEqual(composite.width(), 200)
        self.assertEqual(composite.height(), 200)


class TestPyAVVideoEncodingWithAnnotations(unittest.TestCase):
    """Validates real-time video encoding pipeline with annotated frames and presets."""

    def test_encode_annotated_mp4_file(self) -> None:
        """Creates a real MP4 with PyAV containing drawn shapes and blur redaction."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            mp4_path = Path(tmp_dir) / "test_annotation_video.mp4"
            w, h = 640, 360
            fps = 15

            engine = AnnotationEngine()
            engine.add_item(AnnotationItem(kind="arrow", points=[(100, 100), (300, 200)], color="#38BDF8", stroke_width=4))
            engine.add_item(AnnotationItem(kind="step", x=300, y=200, step_number=1, color="#EF4444"))
            engine.add_item(AnnotationItem(kind="text", x=120, y=80, text="Important Step", color="#FFFFFF"))

            container = av.open(str(mp4_path), mode="w")
            stream = container.add_stream("libx264", rate=fps)
            stream.width = w
            stream.height = h
            stream.pix_fmt = "yuv420p"
            stream.options = {"crf": "26", "preset": "ultrafast"}

            bgra_buf = np.empty((h, w, 4), dtype=np.uint8)

            # Generate 15 frames (1 full second of video)
            for i in range(15):
                # Gradient background
                bgra_buf[:, :, 0] = i * 10       # Blue
                bgra_buf[:, :, 1] = 60           # Green
                bgra_buf[:, :, 2] = 20           # Red
                bgra_buf[:, :, 3] = 255

                engine.render_to_bgra(bgra_buf)
                frame = av.VideoFrame.from_ndarray(bgra_buf, format="bgra")
                frame.pts = i
                for packet in stream.encode(frame):
                    container.mux(packet)

            for packet in stream.encode():
                container.mux(packet)
            container.close()

            self.assertTrue(mp4_path.exists())
            self.assertGreater(mp4_path.stat().st_size, 1024)

            # Validate that the file decodes back cleanly
            with av.open(str(mp4_path)) as read_cont:
                self.assertEqual(len(read_cont.streams.video), 1)
                vs = read_cont.streams.video[0]
                self.assertEqual(vs.codec_context.width, w)
                self.assertEqual(vs.codec_context.height, h)
                decoded_frames = list(read_cont.decode(vs))
                self.assertEqual(len(decoded_frames), 15)


class TestClipboardAndSharingInterop(unittest.TestCase):
    """Validates native Windows clipboard functions."""

    def test_copy_text_to_clipboard(self) -> None:
        test_str = "VOX Screen Recording Token 12345"
        ok = copy_text_to_clipboard(test_str)
        self.assertTrue(ok)
        cb = QApplication.clipboard()
        self.assertEqual(cb.text(), test_str)

    def test_copy_image_to_clipboard(self) -> None:
        qimg = QImage(100, 100, QImage.Format.Format_ARGB32)
        qimg.fill(QColor(16, 185, 129))
        ok = copy_image_to_clipboard(qimg)
        self.assertTrue(ok)
        cb = QApplication.clipboard()
        self.assertFalse(cb.image().isNull())

    def test_copy_file_to_clipboard(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"mock video data")
            temp_path = f.name
        try:
            ok = copy_file_to_clipboard(temp_path)
            self.assertTrue(ok)
            cb = QApplication.clipboard()
            urls = cb.mimeData().urls()
            self.assertTrue(any(Path(u.toLocalFile()).resolve() == Path(temp_path).resolve() for u in urls))
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class TestMarkupStudioAndUIComponents(unittest.TestCase):
    """Validates the Image Markup Studio and Annotation Toolbar instantiation."""

    def test_toolbar_initialization_and_actions(self) -> None:
        from voxlocal.annotation.toolbar import AnnotationToolbar

        tb = AnnotationToolbar()
        self.assertIsNotNone(tb)
        self.assertEqual(tb._active_tool, "pen")

        received_tools = []
        tb.tool_changed.connect(received_tools.append)
        tb.set_tool("arrow")
        self.assertEqual(tb._active_tool, "arrow")
        self.assertIn("arrow", received_tools)

        tb.close()

    def test_markup_studio_initialization_and_composite(self) -> None:
        from voxlocal.annotation.markup_studio import ImageMarkupStudio

        test_img = QImage(800, 600, QImage.Format.Format_ARGB32)
        test_img.fill(QColor(15, 23, 42))

        studio = ImageMarkupStudio(initial_image=test_img, title="Unit Test Studio")
        self.assertIsNotNone(studio)
        self.assertEqual(studio.canvas.base_image.width(), 800)

        # Add markup via engine
        studio.engine.add_item(AnnotationItem(kind="step", x=50, y=50, step_number=1))
        comp = studio.get_composite_image()
        self.assertIsNotNone(comp)
        self.assertEqual(comp.width(), 800)
        self.assertEqual(comp.height(), 600)

        studio.close()


if __name__ == "__main__":
    unittest.main()

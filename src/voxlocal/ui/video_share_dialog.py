"""
VOX Post-Recording Share & Export Modal (Core Feature 2).
Provides instant preview, playback scrubber, metadata inspection, and
one-click sharing to Teams/Outlook via Windows CF_HDROP clipboard file drop.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
from typing import Optional

import av
from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from voxlocal.core.logger import get_logger
from voxlocal.win32.clipboard import copy_file_to_clipboard, copy_text_to_clipboard

logger = get_logger("ui.video_share_dialog")


class VideoShareDialog(QDialog):
    """
    Sleek share and export modal for completed screen recordings.
    """
    def __init__(self, video_path: str, title: str = "Screen Recording", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.video_path = Path(video_path).resolve()
        self.recording_title = title

        self.setWindowTitle(f"VOX — Share {self.recording_title}")
        self.setFixedSize(680, 560)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        # Video metadata cache
        self.duration_sec = 0.0
        self.width_px = 0
        self.height_px = 0
        self.fps = 0
        self.size_mb = 0.0
        self.thumbnail: Optional[QImage] = None

        self._load_video_metadata()
        self._setup_ui()
        self._apply_theme()

    def _load_video_metadata(self) -> None:
        if not self.video_path.exists():
            return

        try:
            self.size_mb = self.video_path.stat().st_size / (1024 * 1024)
            with av.open(str(self.video_path)) as container:
                if container.streams.video:
                    vstream = container.streams.video[0]
                    self.width_px = vstream.codec_context.width
                    self.height_px = vstream.codec_context.height
                    self.fps = int(round(float(vstream.average_rate or 30)))
                    if vstream.duration and vstream.time_base:
                        self.duration_sec = float(vstream.duration * vstream.time_base)

                    # Extract first frame thumbnail
                    for frame in container.decode(vstream):
                        img_arr = frame.to_ndarray(format="rgb24")
                        h, w = img_arr.shape[:2]
                        qimg = QImage(img_arr.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
                        self.thumbnail = qimg
                        break
        except Exception as exc:
            logger.debug("Error reading video metadata: %s", exc)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # 1. Header Title & Success Badge
        hdr_lay = QHBoxLayout()
        hdr_lay.setSpacing(10)

        badge_icon = QLabel("🎬", self)
        badge_icon.setStyleSheet("font-size: 24px;")
        hdr_lay.addWidget(badge_icon)

        t_lay = QVBoxLayout()
        t_lay.setSpacing(2)
        lbl_head = QLabel("Screen Recording Ready", self)
        lbl_head.setStyleSheet("font-size: 17px; font-weight: 700; color: #F8FAFC;")
        lbl_sub = QLabel("Lightweight MP4 generated with synchronized audio and annotations.", self)
        lbl_sub.setStyleSheet("font-size: 12px; color: #94A3B8;")
        t_lay.addWidget(lbl_head)
        t_lay.addWidget(lbl_sub)
        hdr_lay.addLayout(t_lay)
        hdr_lay.addStretch()

        layout.addLayout(hdr_lay)

        # 2. Thumbnail & Preview Frame
        preview_frame = QFrame(self)
        preview_frame.setObjectName("previewFrame")
        preview_frame.setFixedHeight(260)
        p_lay = QVBoxLayout(preview_frame)
        p_lay.setContentsMargins(8, 8, 8, 8)
        p_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_thumb = QLabel(preview_frame)
        self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if self.thumbnail and not self.thumbnail.isNull():
            scaled = self.thumbnail.scaled(
                620,
                244,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.lbl_thumb.setPixmap(QPixmap.fromImage(scaled))
        else:
            self.lbl_thumb.setText("🖥️\nPreview unavailable")
            self.lbl_thumb.setStyleSheet("color: #64748B; font-size: 14px;")

        p_lay.addWidget(self.lbl_thumb)
        layout.addWidget(preview_frame)

        # 3. Metadata Info Strip
        meta_frame = QFrame(self)
        meta_frame.setObjectName("metaFrame")
        m_lay = QHBoxLayout(meta_frame)
        m_lay.setContentsMargins(16, 10, 16, 10)
        m_lay.setSpacing(24)

        dur_str = time.strftime("%M:%S", time.gmtime(self.duration_sec))
        meta_items = [
            ("⏱️ Duration", dur_str),
            ("📐 Resolution", f"{self.width_px} × {self.height_px}"),
            ("⚡ Frame Rate", f"{self.fps} FPS"),
            ("💾 File Size", f"{self.size_mb:.1f} MB"),
        ]

        for label_text, val_text in meta_items:
            item_box = QVBoxLayout()
            item_box.setSpacing(2)
            lbl = QLabel(label_text, meta_frame)
            lbl.setStyleSheet("font-size: 11px; color: #94A3B8; font-weight: 600;")
            val = QLabel(val_text, meta_frame)
            val.setStyleSheet("font-size: 13px; color: #F1F5F9; font-weight: 700;")
            item_box.addWidget(lbl)
            item_box.addWidget(val)
            m_lay.addLayout(item_box)

        m_lay.addStretch()
        layout.addWidget(meta_frame)

        # 4. Action Buttons (Primary Sharing Actions)
        btn_lay = QHBoxLayout()
        btn_lay.setSpacing(10)

        self.btn_copy_file = QPushButton("📋 Copy Video (File Drop)", self)
        self.btn_copy_file.setToolTip("Places video file on clipboard (CF_HDROP) for direct pasting into Teams/Outlook")
        self.btn_copy_file.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_file.setObjectName("primaryBtn")
        self.btn_copy_file.clicked.connect(self._copy_video_file)
        btn_lay.addWidget(self.btn_copy_file, 1)

        self.btn_copy_path = QPushButton("🔗 Copy Path", self)
        self.btn_copy_path.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_path.clicked.connect(self._copy_path)
        btn_lay.addWidget(self.btn_copy_path)

        self.btn_folder = QPushButton("📁 Open Folder", self)
        self.btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_folder.clicked.connect(self._open_folder)
        btn_lay.addWidget(self.btn_folder)

        self.btn_save_as = QPushButton("💾 Save As...", self)
        self.btn_save_as.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_as.clicked.connect(self._save_as)
        btn_lay.addWidget(self.btn_save_as)

        btn_close = QPushButton("Done", self)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(self.accept)
        btn_lay.addWidget(btn_close)

        layout.addLayout(btn_lay)

    def _apply_theme(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background: #0F172A;
            }
            QFrame#previewFrame {
                background: #080D15;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }
            QFrame#metaFrame {
                background: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                color: #F8FAFC;
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.15);
            }
            QPushButton#primaryBtn {
                background: #0284C7;
                color: #FFFFFF;
                border: none;
                font-weight: 700;
            }
            QPushButton#primaryBtn:hover {
                background: #0369A1;
            }
        """)

    # ------------------------------------------------------------------
    # Sharing Action Handlers
    # ------------------------------------------------------------------
    def _copy_video_file(self) -> None:
        if self.video_path.exists():
            ok = copy_file_to_clipboard(self.video_path)
            if ok:
                self.btn_copy_file.setText("✓ Copied to Clipboard!")
                QTimer.singleShot(2500, lambda: self.btn_copy_file.setText("📋 Copy Video (File Drop)"))
            else:
                QMessageBox.warning(self, "Clipboard Error", "Could not copy video file to clipboard.")

    def _copy_path(self) -> None:
        copy_text_to_clipboard(str(self.video_path))
        self.btn_copy_path.setText("✓ Copied!")
        QTimer.singleShot(2000, lambda: self.btn_copy_path.setText("🔗 Copy Path"))

    def _open_folder(self) -> None:
        if self.video_path.exists():
            try:
                subprocess.run(["explorer.exe", f"/select,{self.video_path}"], check=False)
            except Exception as exc:
                logger.error("Could not open explorer: %s", exc)

    def _save_as(self) -> None:
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save Video Recording As",
            str(Path.home() / "Videos" / self.video_path.name),
            "MP4 Video (*.mp4)",
        )
        if dest:
            try:
                import shutil
                shutil.copy2(self.video_path, dest)
                self.btn_save_as.setText("✓ Saved!")
                QTimer.singleShot(2000, lambda: self.btn_save_as.setText("💾 Save As..."))
            except Exception as exc:
                QMessageBox.warning(self, "Save Error", f"Failed to save video: {exc}")

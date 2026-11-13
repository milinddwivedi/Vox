"""
Win32 Rich Clipboard Interoperability for VOX.
Provides universal copy-paste support across Microsoft Teams, Slack, Outlook,
Word, PowerPoint, Jira, and Windows File Explorer using CF_DIB, CF_DIBV5, and CF_HDROP.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Optional, Union

from PyQt6.QtCore import QBuffer, QIODevice, QMimeData, QUrl
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication

from voxlocal.core.logger import get_logger

logger = get_logger("win32.clipboard")


def copy_image_to_clipboard(image_input: Union[QImage, QPixmap, str, Path]) -> bool:
    """
    Places an image onto the Windows clipboard as both a standard CF_DIB bitmap
    and a Qt image, enabling instant Ctrl+V pasting into Teams, Slack, Outlook, Word, etc.
    """
    try:
        qimg: Optional[QImage] = None
        if isinstance(image_input, QPixmap):
            qimg = image_input.toImage()
        elif isinstance(image_input, QImage):
            qimg = image_input
        elif isinstance(image_input, (str, Path)):
            qimg = QImage(str(image_input))

        if qimg is None or qimg.isNull():
            logger.warning("Cannot copy null image to clipboard.")
            return False

        cb = QApplication.clipboard()
        if not cb:
            return False

        # Set image directly via Qt, which natively provides CF_DIB and CF_DIBV5 on Windows
        cb.setImage(qimg)
        logger.info("Image placed on Windows clipboard (CF_DIB & CF_DIBV5).")
        return True

    except Exception as exc:
        logger.error("Error setting image on clipboard: %s", exc)
        return False


def copy_file_to_clipboard(file_path: Union[str, Path]) -> bool:
    """
    Places a file onto the Windows clipboard as a CF_HDROP file-drop object and text path.
    Allows pasting directly into Teams, Slack, Outlook attachments, and Windows Explorer folders.
    """
    p = Path(file_path).resolve()
    if not p.exists():
        logger.warning("Cannot copy non-existent file to clipboard: %s", p)
        return False

    try:
        cb = QApplication.clipboard()
        if not cb:
            return False

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p))])
        mime.setText(str(p))
        cb.setMimeData(mime)

        logger.info("File placed on clipboard as CF_HDROP: %s", p.name)
        return True

    except Exception as exc:
        logger.error("Error copying file to clipboard: %s", exc)
        return False


def copy_text_to_clipboard(text: str) -> bool:
    """Places string on the clipboard."""
    try:
        cb = QApplication.clipboard()
        if cb:
            cb.setText(text)
            return True
        return False
    except Exception as exc:
        logger.error("Error copying text: %s", exc)
        return False

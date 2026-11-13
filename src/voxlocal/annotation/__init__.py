"""
VOX High-Performance Annotation and Markup Subsystem.
Provides real-time screen overlays, vector drawing, physical pixel blur/redaction,
auto-incrementing tutorial steps, snipping tools, and image markup studio.
"""

from voxlocal.annotation.engine import AnnotationEngine, AnnotationItem
from voxlocal.annotation.toolbar import AnnotationToolbar
from voxlocal.annotation.overlay import AnnotationOverlayWindow
from voxlocal.annotation.snipping import SnippingOverlay
from voxlocal.annotation.markup_studio import ImageMarkupStudio

__all__ = [
    "AnnotationEngine",
    "AnnotationItem",
    "AnnotationToolbar",
    "AnnotationOverlayWindow",
    "SnippingOverlay",
    "ImageMarkupStudio",
]

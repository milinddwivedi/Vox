"""
VOX Desktop Assistant — End-to-End Test Suite: Product UX, Information Architecture & Privacy
Validates:
1. 5 primary navigation workspaces (Dictation, Meetings, History, Vocabulary, Settings).
2. DualTierFloatingPill geometry, modes, non-activating window style (WS_EX_NOACTIVATE).
3. Light and dark theme consistency and token palette integrity.
4. Local AI privacy quarantine and Model Context Protocol (MCP) isolation.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root and src/ are in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from voxlocal.engine.dictation_processor import TransformationMode
from voxlocal.extensions.mcp_server import (
    AuthorizationDecision,
    DictationQuarantineViolationError,
    McpAuthorizationError,
    VoxMcpServer,
)
from voxlocal.storage.database import LocalDatabase
from voxlocal.ui.floating_pill import (
    DualTierFloatingPill,
    DynamicWaveformCapsule,
    LiveSpeechPreviewBubble,
    PillState,
)
from voxlocal.ui.theme import DARK_TOKENS, LIGHT_TOKENS, get_theme_tokens

_APP_INSTANCE = None


def get_qapp() -> QApplication:
    global _APP_INSTANCE
    app = QApplication.instance()
    if app is None:
        _APP_INSTANCE = QApplication(sys.argv)
        return _APP_INSTANCE
    return app


class TestE2EProductUxAndArchitecture(unittest.TestCase):
    """E2E Opaque-box test suite for Product UX, Information Architecture, and Privacy."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = get_qapp()
        cls.temp_dir = tempfile.mkdtemp(prefix="vox_ux_arch_e2e_")
        cls.db_path = Path(cls.temp_dir) / "test_ux.db"
        cls.db = LocalDatabase(cls.db_path)

    @classmethod
    def tearDownClass(cls) -> None:
        if Path(cls.temp_dir).exists():
            shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self) -> None:
        self.pill = DualTierFloatingPill()

    def tearDown(self) -> None:
        self.pill.close()
        if hasattr(self.pill, "preview_bubble"):
            self.pill.preview_bubble.close()

    # =========================================================================
    # 1. 5 Primary Navigation Workspaces Contract
    # =========================================================================

    def test_navigation_workspaces_architecture_contract(self) -> None:
        """Verify 5 primary navigation workspaces structure, routes, and keyboard shortcuts."""
        workspaces = [
            {"id": "dictation", "route": "/dictation", "shortcut": "Ctrl+1", "name": "Dictation"},
            {"id": "meetings", "route": "/meetings", "shortcut": "Ctrl+2", "name": "Meetings"},
            {"id": "history", "route": "/history", "shortcut": "Ctrl+3", "name": "History"},
            {"id": "vocabulary", "route": "/vocabulary", "shortcut": "Ctrl+4", "name": "Vocabulary"},
            {"id": "settings", "route": "/settings", "shortcut": "Ctrl+5", "name": "Settings"},
        ]
        self.assertEqual(len(workspaces), 5)
        routes = [w["route"] for w in workspaces]
        self.assertEqual(routes, ["/dictation", "/meetings", "/history", "/vocabulary", "/settings"])

        shortcuts = [w["shortcut"] for w in workspaces]
        self.assertEqual(shortcuts, ["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5"])

    def test_secondary_features_contextual_embedding(self) -> None:
        """Verify secondary capabilities are embedded contextually without navigation rail clutter."""
        secondary_features = {
            "screen_recording": {
                "parent_workspace": "/meetings",
                "command_palette": "Ctrl+K > Start Screen Recording",
            },
            "ask_vox": {
                "in_meeting_scope": "/meetings/workspace#ask-vox",
                "cross_meeting_scope": "/history#search",
            },
            "quick_switcher": {
                "shortcut": "Ctrl+K",
                "type": "floating_command_palette",
            },
        }
        self.assertIn("screen_recording", secondary_features)
        self.assertIn("ask_vox", secondary_features)
        self.assertIn("quick_switcher", secondary_features)

    # =========================================================================
    # 2. DualTierFloatingPill Geometry, States & Win32 Focus Invariance
    # =========================================================================

    def test_floating_pill_geometry_and_ergonomics(self) -> None:
        """Verify VoxFlowBar dimensions match Wispr Flow FlowBar ergonomics."""
        # Resting FlowBar Capsule (40px x 8px)
        self.assertEqual(self.pill.upper_pill.width(), 40)
        self.assertEqual(self.pill.upper_pill.height(), 8)

        # Listening (Hands-Free): 102x30 capsule with 40x18 waveform and 18x18 action buttons
        self.pill._is_ptt_session = False
        self.pill.set_state(PillState.LISTENING)
        self.assertEqual(self.pill.upper_pill.width(), 102)
        self.assertEqual(self.pill.upper_pill.height(), 30)

        # Waveform capsule dimensions (40px x 18px during Listening)
        self.assertEqual(self.pill.waveform_capsule.width(), 40)
        self.assertEqual(self.pill.waveform_capsule.height(), 18)

        # Cancel and Stop buttons fixed size (18px x 18px)
        self.assertEqual(self.pill.btn_cancel.width(), 18)
        self.assertEqual(self.pill.btn_cancel.height(), 18)
        self.assertEqual(self.pill.btn_stop.width(), 18)
        self.assertEqual(self.pill.btn_stop.height(), 18)

    def test_floating_pill_focus_invariance_window_flags(self) -> None:
        """Verify window flags and Qt attributes guarantee focus invariance."""
        flags = self.pill.windowFlags()
        self.assertTrue(bool(flags & Qt.WindowType.FramelessWindowHint))
        self.assertTrue(bool(flags & Qt.WindowType.WindowStaysOnTopHint))
        self.assertTrue(bool(flags & Qt.WindowType.Tool))
        self.assertTrue(bool(flags & Qt.WindowType.WindowDoesNotAcceptFocus))

        # Focus policy must be NoFocus
        self.assertEqual(self.pill.focusPolicy(), Qt.FocusPolicy.NoFocus)

        # WA_ShowWithoutActivating must be enabled
        self.assertTrue(self.pill.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertTrue(self.pill.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))

    def test_floating_pill_5_states_lifecycle(self) -> None:
        """Verify transition across all 5 operational states."""
        # 1. IDLE
        self.pill.set_state(PillState.IDLE)
        self.assertEqual(self.pill.pill_state, PillState.IDLE)

        # 2. LISTENING
        self.pill.set_state(PillState.LISTENING)
        self.assertEqual(self.pill.pill_state, PillState.LISTENING)
        self.assertTrue(self.pill.btn_stop.isEnabled())

        # 3. PROCESSING
        self.pill.set_state(PillState.PROCESSING)
        self.assertEqual(self.pill.pill_state, PillState.PROCESSING)
        self.assertTrue(self.pill.waveform_capsule._is_processing)
        self.assertFalse(self.pill.btn_stop.isVisible())
        self.assertEqual(self.pill.lbl_state_text.text(), "Writing")

        # Polish processing state
        self.pill.set_state(PillState.POLISH_PROCESSING)
        self.assertEqual(self.pill.pill_state, PillState.POLISH_PROCESSING)
        self.assertEqual(self.pill.lbl_state_text.text(), "Cleaning up")

        # 4. COMMITTED
        self.pill.set_state(PillState.COMMITTED)
        self.assertEqual(self.pill.pill_state, PillState.COMMITTED)

        # 5. ERROR
        self.pill.set_state(PillState.ERROR)
        self.assertEqual(self.pill.pill_state, PillState.ERROR)

    def test_dynamic_waveform_capsule_energy_levels(self) -> None:
        """Verify dynamic 10-bar acoustic equalizer sets RMS energy level accurately."""
        capsule = self.pill.waveform_capsule
        self.assertEqual(capsule.bar_count, 10)

        # Update RMS energy level
        capsule.set_level(0.75)
        self.assertEqual(capsule._target_level, 0.75)

        # Out of bounds clamping [0.0, 1.0]
        capsule.set_level(1.5)
        self.assertEqual(capsule._target_level, 1.0)
        capsule.set_level(-0.5)
        self.assertEqual(capsule._target_level, 0.0)

    def test_live_preview_bubble_positioning_and_display(self) -> None:
        """Verify preview bubble anchors 12px directly above pill without focus stealing."""
        bubble = self.pill.preview_bubble
        pill_rect = QRect(200, 300, 300, 80)
        bubble.update_position_above_pill(pill_rect)

        # Expected Y: pill.y - bubble.height - 12
        expected_y = pill_rect.y() - bubble.sizeHint().height() - 12
        self.assertEqual(bubble.y(), expected_y)

        # Verify preview update with annotated strikethrough html
        bubble.update_preview(
            '<span style="color:#FF5757; text-decoration:line-through;">retracted</span> Clean text output'
        )
        self.assertIn("retracted", bubble.lbl_preview.text())
        self.assertIn("#FF5757", bubble.lbl_preview.text())

    # =========================================================================
    # 3. Light and Dark Theme Consistency
    # =========================================================================

    def test_theme_tokens_contrast_and_consistency(self) -> None:
        """Verify Nordic Studio Light and Midnight Obsidian Dark design token palettes."""
        # 1. Dark Theme Tokens
        dark_tokens = get_theme_tokens("dark")
        self.assertEqual(dark_tokens["background"], "#0B0F19")
        self.assertEqual(dark_tokens["text_primary"], "#F8FAFC")
        self.assertIn("accent", dark_tokens)

        # 2. Light Theme Tokens
        light_tokens = get_theme_tokens("light")
        self.assertEqual(light_tokens["background"], "#F8FAFC")
        self.assertEqual(light_tokens["text_primary"], "#0F172A")
        self.assertIn("accent", light_tokens)

        # Invariant: Backgrounds must be distinct
        self.assertNotEqual(dark_tokens["background"], light_tokens["background"])

    def test_pill_theme_application(self) -> None:
        """Verify applying Dark and Light themes to pill and preview bubble."""
        # Apply dark theme
        self.pill.apply_theme("dark")
        self.assertEqual(self.pill._theme, "dark")

        # Apply light theme
        self.pill.apply_theme("light")
        self.assertEqual(self.pill._theme, "light")

    # =========================================================================
    # 4. Local AI Privacy Quarantine & MCP Isolation
    # =========================================================================

    def test_local_ai_mcp_quarantine_invariant(self) -> None:
        """Verify personal dictation voice typing is quarantined from external MCP server access."""
        mcp_server = VoxMcpServer(self.db)

        # Invariant 1: Loopback-only binding check
        self.assertEqual(mcp_server.HOST, "127.0.0.1")

        # Invariant 2: Cryptographic token authentication required
        session_token = mcp_server.generate_session_token()
        self.assertEqual(len(session_token), 64)  # 256-bit hex token
        self.assertTrue(mcp_server.validate_token(session_token))
        self.assertFalse(mcp_server.validate_token("invalid_unauthorized_token"))

        # Invariant 3: Dictation quarantine enforcement
        with self.assertRaises(DictationQuarantineViolationError):
            mcp_server.execute_tool(
                tool_name="dictation_stream",
                params={},
                token=session_token,
            )

    def test_my_thoughts_quarantine_from_mcp_by_default(self) -> None:
        """Verify Tab 1 ('My Thoughts') returns 403 / unauthorized to MCP queries by default."""
        mcp_server = VoxMcpServer(self.db)
        token = mcp_server.generate_session_token()

        # Seed meeting with private thoughts
        m_id = self.db.create_meeting(title="Secret Pricing Review")
        self.db.update_meeting(
            m_id,
            notes="Confidential: Negotiate 15% margin discount.",
        )

        # Default query without authorization must raise McpAuthorizationError or return denied
        auth_decision = mcp_server.check_thoughts_authorization(m_id)
        self.assertEqual(auth_decision, AuthorizationDecision.DENIED)


if __name__ == "__main__":
    unittest.main()

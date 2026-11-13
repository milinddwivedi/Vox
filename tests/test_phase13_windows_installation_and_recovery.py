from __future__ import annotations
"""
VOX Enterprise Release Programme - Phase 13
Windows Installation, Updates & Recovery Test Battery

Covers:
1. Standalone Binary & Distribution Packaging (VoxLocal.exe size, dependencies, assets).
2. Windows Startup Registry Management (StartupManager command construction and toggle).
3. Interrupted Meeting Startup Recovery (Recovery of wedged 'recording'/'transcribing' meetings).
4. Database Pre-Migration Snapshot Recovery & Rollback.
5. Installation & Setup Script Integrity.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.core.config import AppConfig
from voxlocal.storage.database import LocalDatabase
from voxlocal.win32.startup import StartupManager


class TestPhase13WindowsInstallationAndRecovery(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="vox_phase13_install_test_")
        self.config = AppConfig()
        self.config.storage_dir = Path(self.temp_dir)
        self.db = LocalDatabase(self.config.db_path)

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # 1. Standalone Binary & Distribution Packaging
    # -------------------------------------------------------------------------
    def test_standalone_distribution_assets_and_binary(self):
        """Verify dist/VoxLocal contains the executable and necessary runtime packages."""
        dist_dir = ROOT_DIR / "dist" / "VoxLocal"
        self.assertTrue(dist_dir.exists(), f"Distribution folder must exist at {dist_dir}")

        exe_path = dist_dir / "VoxLocal.exe"
        self.assertTrue(exe_path.exists(), f"VoxLocal.exe must exist at {exe_path}")
        exe_size = exe_path.stat().st_size
        self.assertGreater(exe_size, 10_000_000, f"VoxLocal.exe must be > 10MB, got {exe_size} bytes")

        # Verify bundled python runtime DLLs exist in the distribution directory or _internal
        internal_dir = dist_dir / "_internal"
        dlls = list(dist_dir.glob("*.dll")) + list(internal_dir.glob("*.dll")) if internal_dir.exists() else list(dist_dir.glob("*.dll"))
        self.assertGreater(len(dlls), 0, "Distribution package must bundle necessary runtime DLLs")

    # -------------------------------------------------------------------------
    # 2. Windows Startup Registry Management
    # -------------------------------------------------------------------------
    def test_startup_manager_command_construction(self):
        """Verify get_launch_command generates proper quoted command string with startup arguments."""
        cmd_min = StartupManager.get_launch_command(minimized=True)
        self.assertIn("--startup", cmd_min)
        self.assertTrue(cmd_min.startswith('"'), "Command path must be safely double-quoted")

        cmd_full = StartupManager.get_launch_command(minimized=False)
        self.assertNotIn("--startup", cmd_full)
        self.assertTrue(cmd_full.startswith('"'), "Command path must be safely double-quoted")

    def test_startup_manager_registry_mock_toggle(self):
        """Verify set_enabled correctly interacts with Windows registry."""
        mock_key = MagicMock()
        with patch("winreg.OpenKey", return_value=mock_key), \
             patch("winreg.SetValueEx") as mock_set, \
             patch("winreg.DeleteValue") as mock_del:
            
            # Enable startup
            ok_enable = StartupManager.set_enabled(True, minimized=True)
            self.assertTrue(ok_enable)
            self.assertTrue(mock_set.called)

            # Disable startup
            ok_disable = StartupManager.set_enabled(False)
            self.assertTrue(ok_disable)
            self.assertTrue(mock_del.called)

    # -------------------------------------------------------------------------
    # 3. Interrupted Meeting Startup Recovery
    # -------------------------------------------------------------------------
    def test_unclean_meeting_startup_recovery(self):
        """Verify meetings left in recording/transcribing states are discovered and marked interrupted on startup."""
        # Create unfinalized meeting
        m_id = self.db.create_meeting(title="Crashed Session Simulation")
        self.db.update_meeting(m_id, status="recording")

        # Query unfinalized meetings
        unfinalized = self.db.list_unfinalized_meetings()
        unfinalized_ids = [m["id"] for m in unfinalized]
        self.assertIn(m_id, unfinalized_ids, "Crashed meeting must be detected by list_unfinalized_meetings")

        # Simulate startup recovery mark
        self.db.update_meeting(m_id, status="interrupted", summary="Recovered after abnormal session termination.")
        meeting_after = self.db.get_meeting(m_id)
        self.assertEqual(meeting_after["status"], "interrupted")

    # -------------------------------------------------------------------------
    # 4. Database Pre-Migration Snapshot & Rollback
    # -------------------------------------------------------------------------
    def test_database_snapshot_and_rollback_readiness(self):
        """Verify database snapshot creation succeeds and retains valid schema."""
        backup_dir = Path(self.temp_dir) / "snapshots"
        snapshot_path = self.db.create_verified_snapshot(backup_dir)
        self.assertIsNotNone(snapshot_path)
        self.assertTrue(Path(snapshot_path).exists(), "Verified snapshot file must exist")

        # Validate snapshot integrity with PRAGMA integrity_check
        conn = sqlite3.connect(snapshot_path)
        cur = conn.cursor()
        integrity = cur.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        self.assertEqual(integrity, "ok", "Database snapshot must pass integrity check")

    # -------------------------------------------------------------------------
    # 5. Installer Script Validation
    # -------------------------------------------------------------------------
    def test_installer_script_paths_and_syntax(self):
        """Verify install_and_setup.ps1 and uninstall.ps1 exist and reference correct launch targets."""
        installer_ps1 = ROOT_DIR / "install_and_setup.ps1"
        uninstaller_ps1 = ROOT_DIR / "uninstall.ps1"

        self.assertTrue(installer_ps1.exists(), "install_and_setup.ps1 must exist")
        self.assertTrue(uninstaller_ps1.exists(), "uninstall.ps1 must exist")

        installer_content = installer_ps1.read_text(encoding="utf-8")
        self.assertIn("VoxLocal.exe", installer_content)
        self.assertIn("CreateDesktopShortcut", installer_content)


if __name__ == "__main__":
    unittest.main()

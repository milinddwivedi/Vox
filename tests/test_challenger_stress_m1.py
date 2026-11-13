"""
Challenger 1 Adversarial Stress Test Suite for Milestone 1.
Empirically stress-tests StartupManager under adverse OS/registry conditions:
1. Permission denied (WinError 5 / EACCES)
2. File/Key not found (WinError 2 / ENOENT)
3. Arbitrary OS / Winreg errors (WinError 14, 22, winreg.error)
4. Unexpected runtime exceptions (RuntimeError, ValueError, TypeError)
5. Rapid toggling single-fire warning latch (_warned_access_denied)
6. Multi-threaded concurrent access under failure injection
7. Launch command path resolution and parameter handling
8. Live host system capability inspection and roundtrip
"""

from __future__ import annotations

import concurrent.futures
import logging
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import MagicMock, patch
import winreg

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voxlocal.win32.startup import APP_REG_NAME, RUN_KEY_PATH, StartupManager


class StartupManagerStressTestSuite(unittest.TestCase):
    """Adversarial stress harness for Windows StartupManager."""

    def setUp(self) -> None:
        self.original_latch = StartupManager._warned_access_denied
        StartupManager._warned_access_denied = False

    def tearDown(self) -> None:
        StartupManager._warned_access_denied = self.original_latch

    def test_is_available_never_raises_on_adverse_conditions(self) -> None:
        """Verify is_available() catches every expected and unexpected failure mode."""
        adverse_exceptions = [
            PermissionError(5, "Access is denied"),
            PermissionError(13, "Permission denied"),
            FileNotFoundError(2, "The system cannot find the file specified"),
            OSError(22, "The parameter is incorrect"),
            OSError(14, "Not enough memory resources are available"),
            RuntimeError("Unexpected internal winreg failure"),
            ValueError("Invalid registry access mask"),
            TypeError("Incompatible handle type"),
            winreg.error(5, "Access is denied"),
        ]

        for exc in adverse_exceptions:
            with patch("winreg.OpenKey", side_effect=exc):
                try:
                    result = StartupManager.is_available()
                    self.assertFalse(
                        result,
                        f"is_available() should return False for {type(exc).__name__}: {exc}",
                    )
                except Exception as unexpected:
                    self.fail(
                        f"is_available() raised unhandled exception {type(unexpected).__name__}: {unexpected} on {exc}"
                    )

    def test_is_enabled_never_raises_on_adverse_conditions(self) -> None:
        """Verify is_enabled() never raises under open key failure or query failure."""
        adverse_open_exceptions = [
            PermissionError(5, "Access is denied"),
            FileNotFoundError(2, "Key does not exist"),
            OSError(5, "General OS Error"),
            RuntimeError("Unexpected OS crash"),
            Exception("Generic unknown error"),
        ]

        for exc in adverse_open_exceptions:
            with patch("winreg.OpenKey", side_effect=exc):
                try:
                    self.assertFalse(StartupManager.is_enabled())
                except Exception as unexpected:
                    self.fail(
                        f"is_enabled() raised unhandled exception {type(unexpected).__name__} on OpenKey {exc}"
                    )

        # OpenKey succeeds, but QueryValueEx fails
        adverse_query_exceptions = [
            FileNotFoundError(2, "Value does not exist"),
            PermissionError(5, "Access is denied"),
            OSError(14, "Out of memory"),
            RuntimeError("Fatal query error"),
            Exception("Generic query fault"),
        ]

        for exc in adverse_query_exceptions:
            mock_key = MagicMock()
            with patch("winreg.OpenKey", return_value=mock_key), \
                 patch("winreg.QueryValueEx", side_effect=exc):
                try:
                    self.assertFalse(StartupManager.is_enabled())
                except Exception as unexpected:
                    self.fail(
                        f"is_enabled() raised unhandled exception on QueryValueEx {exc}"
                    )

        # QueryValueEx returns empty string or valid string
        mock_key = MagicMock()
        with patch("winreg.OpenKey", return_value=mock_key), \
             patch("winreg.QueryValueEx", return_value=("", winreg.REG_SZ)):
            self.assertFalse(StartupManager.is_enabled())

        with patch("winreg.OpenKey", return_value=mock_key), \
             patch("winreg.QueryValueEx", return_value=('"C:\\app.exe" --startup', winreg.REG_SZ)):
            self.assertTrue(StartupManager.is_enabled())

    def test_set_enabled_true_never_raises_on_adverse_conditions(self) -> None:
        """Verify set_enabled(True) never raises under any adverse conditions."""
        adverse_failures = [
            PermissionError(5, "Access is denied"),
            FileNotFoundError(2, "Parent key not found"),
            OSError(5, "Access is denied"),
            RuntimeError("Registry corrupted"),
            Exception("Unforeseen system error"),
        ]

        # Fail during OpenKey
        for exc in adverse_failures:
            with patch("winreg.OpenKey", side_effect=exc):
                try:
                    ok = StartupManager.set_enabled(True, minimized=True)
                    self.assertFalse(ok)
                except Exception as unexpected:
                    self.fail(
                        f"set_enabled(True) raised {type(unexpected).__name__} during OpenKey {exc}"
                    )

        # OpenKey succeeds, but SetValueEx fails
        for exc in adverse_failures:
            mock_key = MagicMock()
            with patch("winreg.OpenKey", return_value=mock_key), \
                 patch("winreg.SetValueEx", side_effect=exc):
                try:
                    ok = StartupManager.set_enabled(True, minimized=True)
                    self.assertFalse(ok)
                except Exception as unexpected:
                    self.fail(
                        f"set_enabled(True) raised {type(unexpected).__name__} during SetValueEx {exc}"
                    )

    def test_set_enabled_false_never_raises_on_adverse_conditions(self) -> None:
        """Verify set_enabled(False) never raises and treats missing value as clean success."""
        # 1. Missing value should succeed (no-op delete)
        mock_key = MagicMock()
        with patch("winreg.OpenKey", return_value=mock_key), \
             patch("winreg.DeleteValue", side_effect=FileNotFoundError(2, "Value not found")):
            self.assertTrue(
                StartupManager.set_enabled(False),
                "Deleting non-existent startup registration should return True (clean no-op)",
            )

        # 2. PermissionError or OS errors during DeleteValue must return False without throwing
        adverse_delete_failures = [
            PermissionError(5, "Access is denied"),
            OSError(5, "Access is denied"),
            RuntimeError("Registry lock collision"),
            Exception("Unforeseen delete error"),
        ]

        for exc in adverse_delete_failures:
            mock_key = MagicMock()
            with patch("winreg.OpenKey", return_value=mock_key), \
                 patch("winreg.DeleteValue", side_effect=exc):
                try:
                    ok = StartupManager.set_enabled(False)
                    self.assertFalse(ok)
                except Exception as unexpected:
                    self.fail(
                        f"set_enabled(False) raised {type(unexpected).__name__} on DeleteValue {exc}"
                    )

    def test_rapid_toggling_single_fire_warning_latch(self) -> None:
        """Verify 50 rapid calls to set_enabled emit exactly 1 warning log via single-fire latch."""
        StartupManager._warned_access_denied = False

        log_records: list[logging.LogRecord] = []

        class ListHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                log_records.append(record)

        target_logger = logging.getLogger("voxlocal.win32.startup")
        old_level = target_logger.level
        target_logger.setLevel(logging.DEBUG)
        handler = ListHandler()
        target_logger.addHandler(handler)

        try:
            with patch(
                "winreg.OpenKey",
                side_effect=PermissionError(5, "Access is denied [WinError 5]"),
            ):
                # Perform 50 rapid toggles alternating True and False
                for i in range(50):
                    toggle = (i % 2 == 0)
                    result = StartupManager.set_enabled(toggle)
                    self.assertFalse(result)

            warnings = [r for r in log_records if r.levelno == logging.WARNING]
            debugs = [r for r in log_records if r.levelno == logging.DEBUG]

            self.assertEqual(
                len(warnings),
                1,
                f"Expected exactly 1 WARNING log across 50 calls, found {len(warnings)}: {[w.getMessage() for w in warnings]}",
            )
            self.assertIn("Windows startup registry modification access denied", warnings[0].getMessage())
            self.assertGreaterEqual(
                len(debugs),
                49,
                f"Expected at least 49 DEBUG logs suppressing repeated warning, found {len(debugs)}",
            )
            self.assertTrue(StartupManager._warned_access_denied)
        finally:
            target_logger.removeHandler(handler)
            target_logger.setLevel(old_level)

    def test_multithreaded_concurrent_access_under_hostile_faults(self) -> None:
        """Stress-test concurrent calls from 10 threads under randomized fault injection."""
        errors: list[Exception] = []
        possible_faults = [
            PermissionError(5, "Access is denied"),
            FileNotFoundError(2, "File not found"),
            OSError(5, "OS failure"),
            RuntimeError("Thread collision"),
            None,  # Success simulation
        ]

        def worker_task(thread_id: int) -> None:
            mock_key = MagicMock()
            for _ in range(25):
                fault = random.choice(possible_faults)
                if fault is not None:
                    with patch("winreg.OpenKey", side_effect=fault):
                        try:
                            StartupManager.is_available()
                            StartupManager.is_enabled()
                            StartupManager.set_enabled(True)
                            StartupManager.set_enabled(False)
                        except Exception as e:
                            errors.append(e)
                else:
                    with patch("winreg.OpenKey", return_value=mock_key), \
                         patch("winreg.SetValueEx"), \
                         patch("winreg.DeleteValue"), \
                         patch("winreg.QueryValueEx", return_value=('"test.exe"', winreg.REG_SZ)):
                        try:
                            StartupManager.is_available()
                            StartupManager.is_enabled()
                            StartupManager.set_enabled(True)
                            StartupManager.set_enabled(False)
                        except Exception as e:
                            errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker_task, tid) for tid in range(10)]
            concurrent.futures.wait(futures)

        self.assertEqual(
            len(errors),
            0,
            f"Encountered {len(errors)} unhandled exceptions during multithreaded stress test: {errors[:5]}",
        )

    def test_get_launch_command_handling(self) -> None:
        """Verify command formatting handles frozen/unfrozen states and minimized args safely."""
        cmd_min = StartupManager.get_launch_command(minimized=True)
        self.assertTrue(cmd_min.startswith('"'), "Command path must start with double quote")
        self.assertIn("--startup", cmd_min)

        cmd_full = StartupManager.get_launch_command(minimized=False)
        self.assertTrue(cmd_full.startswith('"'))
        self.assertNotIn("--startup", cmd_full)

        # Test simulated frozen executable
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", r"C:\Program Files\VOX\VoxLocal.exe"):
            frozen_cmd = StartupManager.get_launch_command(minimized=True)
            self.assertEqual(frozen_cmd, r'"C:\Program Files\VOX\VoxLocal.exe" --startup')

    def test_live_host_environment_behavior(self) -> None:
        """Query real live host registry capability and verify consistency."""
        can_modify = StartupManager.is_available()
        # Ensure is_available returned a pure bool
        self.assertIsInstance(can_modify, bool)

        if can_modify:
            initial_state = StartupManager.is_enabled()
            try:
                # Test live enable
                self.assertTrue(StartupManager.set_enabled(True, minimized=True))
                self.assertTrue(StartupManager.is_enabled())
                # Test live disable
                self.assertTrue(StartupManager.set_enabled(False))
                self.assertFalse(StartupManager.is_enabled())
            finally:
                StartupManager.set_enabled(initial_state)
        else:
            # When host denies access, calls must return False without crashing
            self.assertFalse(StartupManager.is_enabled())
            self.assertFalse(StartupManager.set_enabled(True))
            self.assertFalse(StartupManager.set_enabled(False))


if __name__ == "__main__":
    unittest.main(verbosity=2)

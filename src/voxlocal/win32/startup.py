"""
Windows Login Startup Manager.
Configures automatic launch at Windows login via HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.
Requires no Administrator privileges and supports clean enable/disable with graceful
sandbox and restricted-permission fallback.
"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

from voxlocal.core.logger import get_app_root, get_logger

logger = get_logger("win32.startup")

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_REG_NAME = "VoxLocalVoiceAssistant"


class StartupManager:
    """Manages Windows user-level startup registration."""

    _warned_access_denied: bool = False

    @staticmethod
    def get_launch_command(minimized: bool = True) -> str:
        """Build the command string written to HKCU\\...\\Run."""
        if getattr(sys, "frozen", False):
            exe_path = Path(sys.executable).resolve()
            args = " --startup" if minimized else ""
            return f'"{exe_path}"{args}'

        root = get_app_root()
        pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
        if not pythonw.exists():
            pythonw = Path(sys.executable).resolve()
        main_script = root / "main.py"
        args = " --startup" if minimized else ""
        return f'"{pythonw}" "{main_script}"{args}'

    @classmethod
    def is_available(cls) -> bool:
        """Check whether the Windows Run registry key can be opened for modification."""
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
            ):
                return True
        except (PermissionError, OSError):
            return False
        except Exception as exc:
            logger.debug("Startup registry availability check failed: %s", exc)
            return False

    @classmethod
    def is_enabled(cls) -> bool:
        """Check if Windows login startup is enabled in HKCU\\...\\Run.

        Returns False if disabled, not registered, or if registry access is denied.
        """
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_QUERY_VALUE
            ) as key:
                val, _ = winreg.QueryValueEx(key, APP_REG_NAME)
                return bool(val)
        except FileNotFoundError:
            return False
        except (PermissionError, OSError) as exc:
            logger.debug("Startup registry read access restricted/denied: %s", exc)
            return False
        except Exception as exc:
            logger.warning("Error checking startup registry key: %s", exc)
            return False

    @classmethod
    def set_enabled(cls, enabled: bool, minimized: bool = True) -> bool:
        """Enable or disable Windows login startup registration.

        Returns True if successful, False if registry access failed or was denied.
        """
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
            ) as key:
                if enabled:
                    cmd = cls.get_launch_command(minimized=minimized)
                    winreg.SetValueEx(key, APP_REG_NAME, 0, winreg.REG_SZ, cmd)
                    logger.info("Enabled Windows login startup: %s", cmd)
                else:
                    try:
                        winreg.DeleteValue(key, APP_REG_NAME)
                        logger.info("Disabled Windows login startup.")
                    except FileNotFoundError:
                        pass
            return True
        except (PermissionError, OSError) as exc:
            if not cls._warned_access_denied:
                cls._warned_access_denied = True
                logger.warning(
                    "Windows startup registry modification access denied (sandboxed or restricted permissions): %s",
                    exc,
                )
            else:
                logger.debug(
                    "Windows startup registry modification access denied (suppressed repeated warning): %s",
                    exc,
                )
            return False
        except Exception as exc:
            logger.warning("Failed updating Windows login startup: %s", exc)
            return False

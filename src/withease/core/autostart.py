"""Start WithEase automatically at login – per user, no admin rights.

Windows uses the current user's ``Run`` registry key; Linux (and other POSIX
desktops) use a ``~/.config/autostart/withease.desktop`` entry per the XDG
autostart spec.  The public API (:func:`is_enabled`, :func:`set_enabled`) is
identical on both.
"""
from __future__ import annotations

import sys

_VALUE_NAME = "WithEase"


# ===========================================================================
# Windows – HKCU\...\Run
# ===========================================================================
if sys.platform == "win32":
    import winreg

    _RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def _launch_command() -> str:
        exe = sys.executable
        # Prefer the windowless interpreter so no console flashes at login.
        if exe.lower().endswith("python.exe"):
            exe = exe[:-len("python.exe")] + "pythonw.exe"
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        return f'"{exe}" -m withease'

    def is_enabled() -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, _VALUE_NAME)
            return True
        except OSError:
            return False

    def set_enabled(enabled: bool) -> bool:
        """Enable/disable autostart. Returns True on success."""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                if enabled:
                    winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ,
                                      _launch_command())
                else:
                    try:
                        winreg.DeleteValue(key, _VALUE_NAME)
                    except FileNotFoundError:
                        pass
            return True
        except OSError:
            return False


# ===========================================================================
# POSIX / Linux – XDG autostart .desktop file
# ===========================================================================
else:
    import os
    from pathlib import Path

    def _autostart_file() -> Path:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        return Path(base) / "autostart" / "withease.desktop"

    def _launch_command() -> str:
        if getattr(sys, "frozen", False):
            return sys.executable
        return f'{sys.executable} -m withease'

    def is_enabled() -> bool:
        return _autostart_file().exists()

    def set_enabled(enabled: bool) -> bool:
        """Enable/disable autostart. Returns True on success."""
        path = _autostart_file()
        try:
            if enabled:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=WithEase\n"
                    f"Exec={_launch_command()}\n"
                    "Terminal=false\n"
                    "X-GNOME-Autostart-enabled=true\n",
                    encoding="utf-8",
                )
            elif path.exists():
                path.unlink()
            return True
        except OSError:
            return False

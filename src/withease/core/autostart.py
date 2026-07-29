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
    import os
    import winreg

    _RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def _launch_command() -> str:
        exe = sys.executable
        # Prefer the windowless interpreter so no console flashes at login.
        if exe.lower().endswith("python.exe"):
            exe = exe[:-len("python.exe")] + "pythonw.exe"
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        # Source checkout: at login there is no PYTHONPATH, and the package may
        # not be pip-installed, so bootstrap sys.path from the *running* source
        # directory before launching.  This also pins the exact copy in use.
        try:
            import withease
            src = os.path.dirname(
                os.path.dirname(os.path.abspath(withease.__file__)))
        except Exception:
            src = ""
        if src:
            boot = (f"import sys, runpy; sys.path.insert(0, r'{src}'); "
                    "runpy.run_module('withease', run_name='__main__')")
            return f'"{exe}" -c "{boot}"'
        return f'"{exe}" -m withease'

    # -- Startup-folder fallback (used when the Run key is blocked) ---------

    def _startup_vbs() -> str:
        return os.path.join(
            os.environ.get("APPDATA", ""), "Microsoft", "Windows",
            "Start Menu", "Programs", "Startup", "WithEase.vbs")

    def _write_startup_vbs() -> bool:
        # Launch the command windowless via WScript (double quotes are doubled).
        inner = _launch_command().replace('"', '""')
        content = ("' WithEase Autostart (fensterlos)\r\n"
                   'Set sh = CreateObject("WScript.Shell")\r\n'
                   f'sh.Run "{inner}", 0, False\r\n')
        try:
            path = _startup_vbs()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="ascii", errors="replace") as f:
                f.write(content)
            return True
        except Exception as exc:                       # noqa: BLE001 (diagnostic)
            _diag(f"startup .vbs write failed: {type(exc).__name__}: {exc}")
            return False

    def _remove_startup_vbs() -> None:
        try:
            path = _startup_vbs()
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def _diag(msg: str) -> None:
        try:
            sys.stderr.write(f"[WithEase autostart] {msg}\n")
            sys.stderr.flush()
        except Exception:
            pass

    def _registry_set(enabled: bool) -> bool:
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
        except Exception as exc:                       # noqa: BLE001 (diagnostic)
            _diag(f"registry write failed: {type(exc).__name__}: {exc}")
            return False

    def _registry_has() -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, _VALUE_NAME)
            return True
        except OSError:
            return False

    def is_enabled() -> bool:
        return _registry_has() or os.path.exists(_startup_vbs())

    def set_enabled(enabled: bool) -> bool:
        """Enable/disable autostart. Returns True on success.

        Prefers the Run registry key; if writing it is blocked (some security
        software forbids Run-key edits), falls back to a Startup-folder script."""
        if enabled:
            if _registry_set(True):
                _remove_startup_vbs()      # avoid a duplicate autostart
                return True
            if _write_startup_vbs():
                return True
            _diag("blocked: neither the Run key nor the Startup folder is "
                  "writable (a security program is likely preventing autostart)")
            return False
        ok = _registry_set(False)
        _remove_startup_vbs()
        return ok or True


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

"""Entry point for WithEase.

Run with:
    python -m withease
or after installation:
    withease

Dev flag:
    python -m withease --dev
    Opens the settings window immediately.
    Remove before v1.0 release.
"""
from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from withease import __version__
from withease.app import WithEaseApp
from withease.core import config as _config
from withease.core.resources import app_icon_path


def _setup_logging() -> None:
    """Log to %APPDATA%/WithEase/withease.log.

    The app usually runs under pythonw (no console), so silent stderr logging
    is invisible – errors like a failing profile save would go unnoticed.
    """
    _config.ensure_dirs()
    logging.basicConfig(
        filename=str(_config.CONFIG_DIR / "withease.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Unhandled exceptions land in the log too instead of vanishing.
    sys.excepthook = lambda *exc: logging.critical(
        "unhandled exception", exc_info=exc)

_MUTEX_NAME = "Global\\WithEase_SingleInstance"


def _acquire_single_instance_mutex() -> object | None:
    """Create a named Windows mutex to prevent multiple instances.

    Returns the mutex handle if this is the first instance, or None if
    WithEase is already running.
    """
    mutex = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    last_error = ctypes.windll.kernel32.GetLastError()

    ERROR_ALREADY_EXISTS = 183
    if last_error == ERROR_ALREADY_EXISTS:
        ctypes.windll.kernel32.CloseHandle(mutex)
        return None
    return mutex


def main() -> None:
    _setup_logging()
    logging.info("WithEase starting (argv=%s)", sys.argv)
    dev_mode = "--dev" in sys.argv

    # Single-instance check applies in dev mode too: two instances would
    # each hold the full profile in memory and overwrite each other's saved
    # settings (last writer wins with stale data).
    mutex = _acquire_single_instance_mutex()
    if mutex is None:
        app = QApplication(sys.argv)
        app.setApplicationName("WithEase")
        QMessageBox.information(
            None,
            "WithEase",
            "WithEase läuft bereits im Hintergrund.\n"
            "Du findest es im Systemtray (unten rechts).",
        )
        sys.exit(0)

    # Distinct taskbar identity so Windows shows OUR icon (not python's) and
    # groups our windows correctly.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "WithEase.App")
    except Exception:
        pass

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("WithEase")
    qt_app.setApplicationVersion(__version__)
    qt_app.setQuitOnLastWindowClosed(False)
    icon = app_icon_path()
    if icon.exists():
        qt_app.setWindowIcon(QIcon(str(icon)))

    _app = WithEaseApp(qt_app)

    # Open the settings window on launch when requested – used by the module
    # store's restart, so the user lands back where they were instead of a
    # silent tray start.
    if dev_mode or "--open-settings" in sys.argv:
        _app.show_settings()

    exit_code = qt_app.exec()

    if mutex is not None:
        ctypes.windll.kernel32.ReleaseMutex(mutex)
        ctypes.windll.kernel32.CloseHandle(mutex)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

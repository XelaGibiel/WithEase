"""Entry point for AccessMate.

Run with:
    python -m accessmate
or after installation:
    accessmate
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from accessmate.app import AccessMateApp


def main() -> None:
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("AccessMate")
    qt_app.setApplicationVersion("0.1.0")
    qt_app.setQuitOnLastWindowClosed(False)  # keep running in tray after GUI closes

    _app = AccessMateApp(qt_app)

    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()

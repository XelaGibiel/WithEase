"""Locating bundled package resources (icons, …).

Works both in a normal run and in a PyInstaller build, where data files are
unpacked next to the frozen app under ``sys._MEIPASS/accessmate/``.
"""
from __future__ import annotations

import sys
from pathlib import Path


def package_dir() -> Path:
    """The installed ``accessmate`` package directory (or its frozen mirror)."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base) / "accessmate"
    return Path(__file__).resolve().parents[1]   # …/accessmate


def app_icon_path() -> Path:
    """Absolute path to the AccessMate application icon (.ico)."""
    return package_dir() / "assets" / "icons" / "accessmate.ico"

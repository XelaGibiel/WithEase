"""Locating bundled package resources (icons, …).

Works both in a normal run and in a PyInstaller build, where data files are
unpacked next to the frozen app under ``sys._MEIPASS/withease/``.
"""
from __future__ import annotations

import sys
from pathlib import Path


def package_dir() -> Path:
    """The installed ``withease`` package directory (or its frozen mirror)."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base) / "withease"
    return Path(__file__).resolve().parents[1]   # …/withease


def app_icon_path() -> Path:
    """Absolute path to the WithEase application icon (.ico)."""
    return package_dir() / "assets" / "icons" / "withease.ico"


def app_svg_path() -> Path:
    """Absolute path to the vector logo (.svg) – render this for crisp display
    at any size instead of scaling the .ico."""
    return package_dir() / "assets" / "icons" / "withease.svg"

"""Internationalization (i18n) system.

Usage anywhere in the codebase:
    from withease.core.i18n import tr
    label = tr("module.mouse.name")

Adding a new language:
    1. Copy src/withease/locales/en.json to <lang_code>.json
    2. Translate all values (keep the keys unchanged)
    3. The language will appear automatically in the settings dropdown
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from withease.core.event_bus import bus


def _locales_dir() -> Path:
    """Where the *.json locale files live.

    In a normal run this is the package's locales/ folder.  In a PyInstaller
    build the files are unpacked next to the frozen app (sys._MEIPASS), so we
    resolve them there instead.
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base) / "withease" / "locales"
    return Path(__file__).parent.parent / "locales"


LOCALES_DIR = _locales_dir()
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "de": "Deutsch",
}
_DEFAULT_LANG = "en"
_strings: dict[str, str] = {}
_fallback: dict[str, str] = {}


def load(lang_code: str) -> None:
    """Load a language. Falls back to English for missing keys."""
    global _strings, _fallback

    _fallback = _load_file(_DEFAULT_LANG)

    if lang_code == _DEFAULT_LANG:
        _strings = _fallback
    else:
        _strings = {**_fallback, **_load_file(lang_code)}

    bus.publish("i18n.language_changed", lang=lang_code)


def _load_file(lang_code: str) -> dict[str, str]:
    path = LOCALES_DIR / f"{lang_code}.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data: dict = json.load(f)
    return {k: v for k, v in data.items() if k != "_meta"}


def tr(key: str, **kwargs: str) -> str:
    """Translate a key. Supports simple placeholder substitution:
        tr("greeting", name="Alice")  ->  "Hello, Alice!"
    """
    text = _strings.get(key) or _fallback.get(key) or key
    if kwargs:
        for placeholder, value in kwargs.items():
            text = text.replace(f"{{{placeholder}}}", value)
    return text


# Load English by default so tr() works even before settings are read.
load(_DEFAULT_LANG)

"""The dictation window must not fall back to German literals.

Its texts used to sit hard-coded in the source, so the window stayed German
whatever the app was set to.  They now live in dict_i18n.py.  These tests keep
it that way: a new caption typed straight into the source would be invisible in
every other language, and nobody notices that while working in German.
"""
import ast
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_DICTATION = Path(__file__).resolve().parent.parent / "examples" / "dictation"
sys.path.insert(0, str(_DICTATION))

import dict_i18n  # noqa: E402

# Calls and constructors whose string argument reaches the user.
_SHOWN = {
    "setText", "setToolTip", "setWindowTitle", "setPlaceholderText",
    "setAccessibleName", "_set_hint", "_report", "QPushButton", "QLabel",
    "QCheckBox", "QListWidgetItem", "QGroupBox",
}
# German that is INPUT, not interface: the words the user speaks to confirm or
# cancel in the correction window.  Translating those would break the feature.
_SPOKEN = {"übernehmen", "korrektur übernehmen", "schließen", "abbrechen",
           "uebernehmen", "schliessen", "abbruch", "verwerfen", "fertig",
           "korrigieren", "passt"}

_GERMAN_CHARS = "äöüßÄÖÜ"


def _german_ui_literals(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.attr if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", ""))
        if name not in _SHOWN:
            continue
        for arg in node.args:
            if not (isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)):
                continue
            text = arg.value
            if text.strip().lower() in _SPOKEN:
                continue
            if any(ch in text for ch in _GERMAN_CHARS):
                found.append((arg.lineno, " ".join(text.split())[:70]))
    return found


@pytest.fixture(autouse=True)
def _restore_language():
    """Put the language back afterwards.  Switching it is global state, and a
    test that leaves English behind makes every later test that reads a German
    caption fail – with the blame landing on the wrong file."""
    from withease.core.event_bus import bus
    before = dict_i18n._lang.code
    yield
    bus.publish("i18n.language_changed", lang=before)


@pytest.mark.parametrize("name", ["dictation_window.py", "settings_dialogs.py"])
def test_no_german_left_in_the_source(name):
    leftovers = _german_ui_literals(_DICTATION / name)
    assert not leftovers, (
        f"{name}: these texts would stay German in every language – "
        f"put them in dict_i18n.py: {leftovers}")


def test_both_languages_define_the_same_keys():
    german = set(dict_i18n.STRINGS["de"])
    english = set(dict_i18n.STRINGS["en"])
    assert german == english, {
        "nur deutsch": sorted(german - english),
        "nur englisch": sorted(english - german),
    }


def test_placeholders_match_between_the_languages():
    """A placeholder missing in one language leaves a hole in the sentence."""
    import re
    holes = {}
    for key, german in dict_i18n.STRINGS["de"].items():
        english = dict_i18n.STRINGS["en"][key]
        a = set(re.findall(r"\{(\w+)\}", german))
        b = set(re.findall(r"\{(\w+)\}", english))
        if a != b:
            holes[key] = (sorted(a), sorted(b))
    assert not holes, holes


def test_a_missing_key_falls_back_instead_of_crashing():
    assert dict_i18n.t("does.not.exist") == "does.not.exist"


def test_the_language_follows_the_app():
    from withease.core.event_bus import bus
    bus.publish("i18n.language_changed", lang="de")
    assert dict_i18n.t("win.clear") == "🧹 Leeren"
    bus.publish("i18n.language_changed", lang="en")
    assert dict_i18n.t("win.clear") == "🧹 Empty"

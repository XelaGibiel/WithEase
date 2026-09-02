"""Every tooltip must be wrapped, or Qt renders it as one endless line.

Qt's tooltip label switches word wrapping on only for RICH text
(``setWordWrap(Qt::mightBeRichText(text))``).  A plain-text tooltip therefore
gets no wrapping at all and is laid out on a single line, however long it is –
a two-sentence explanation then runs from one screen edge to the other.

``ui_utils.wrap_tooltip`` renders the text into a ``<table width="N">``, which
makes it rich text (wrapping on) AND pins the measure to a readable width.
The dictation add-on had 28 tooltips and not one of them went through it.
"""
import os
import re
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_DICTATION = Path(__file__).resolve().parent.parent / "examples" / "dictation"
sys.path.insert(0, str(_DICTATION))

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from withease.gui import theme  # noqa: E402
from withease.gui.ui_utils import wrap_tooltip  # noqa: E402

# The only setToolTip calls that must NOT be wrapped: they sit inside the
# fallbacks that run precisely when wrap_tooltip is unavailable (an add-on next
# to an older core), where wrapping is impossible by definition.
_ALLOWED_UNWRAPPED = {("module.py", "lbl.setToolTip(tooltip)"),
                      ("module.py", "checkbox.setToolTip(tooltip)")}

_CALL = re.compile(r"^.*\.setToolTip\($", re.MULTILINE)


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    theme.apply_theme(instance, "dark", font_pt=10)
    return instance


def _as_qt_lays_it_out(text: str) -> int:
    """Width Qt gives this tooltip – word wrap only when it is rich text."""
    label = QLabel()
    label.setWordWrap(text.lstrip().startswith("<"))
    label.setText(text)
    return label.sizeHint().width()


def test_a_long_tooltip_is_pinned_to_a_reading_width(app):
    long_text = (
        "Formuliere den folgenden Text höflich und klar um, ohne den Sinn zu "
        "verändern. Achte auf korrekte Rechtschreibung und Zeichensetzung und "
        "behalte die Anrede bei. Gib nur den überarbeiteten Text zurück.")
    plain = _as_qt_lays_it_out(long_text)
    wrapped = _as_qt_lays_it_out(wrap_tooltip(long_text))
    # The exact number depends on the font; what matters is that the
    # unwrapped version is a long single line and the wrapped one is not.
    assert plain > 700, "the unwrapped line is what this test is about"
    assert wrapped <= 600
    assert wrapped < plain / 2


def test_a_short_tooltip_is_not_blown_up_to_a_box(app):
    """Pinning the long ones must not stretch the short ones into empty space."""
    short = "Fenster schließen (Strg+W)"
    assert _as_qt_lays_it_out(wrap_tooltip(short)) < 400


@pytest.mark.parametrize("name", ["dictation_window.py", "settings_dialogs.py",
                                  "module.py"])
def test_every_tooltip_in_the_add_on_goes_through_the_wrapper(name):
    source = (_DICTATION / name).read_text(encoding="utf-8")
    offenders = []
    for line in source.splitlines():
        stripped = line.strip()
        if ".setToolTip(" not in stripped:
            continue
        after = stripped.split(".setToolTip(", 1)[1].lstrip()
        if after.startswith(("_wrap_tip(", "wrap_tooltip(")) or after == "":
            continue          # wrapped, or the argument is on the next line
        if (name, stripped) in _ALLOWED_UNWRAPPED:
            continue
        offenders.append(stripped)
    assert not offenders, (
        f"{name}: these tooltips would be rendered as one endless line – "
        f"wrap them in _wrap_tip(): {offenders}")

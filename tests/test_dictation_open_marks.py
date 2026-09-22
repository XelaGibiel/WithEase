"""While a quote or bracket is open, a note under the caret says so - and
which words close it.  "Anführungsstriche" sets „ first and “ second, so it
has to be visible which of the two comes next."""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import commands_de as cde  # noqa: E402
import dictation_window as dw  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    w = dw.DictationWindow(on_insert=lambda _t: True, on_copy=lambda _t: None)
    w.show()
    yield w
    w.close()


@pytest.mark.parametrize("before, marks", [
    ("", []),
    ("Er sagte „Hallo", ["„"]),
    ("Er sagte „Hallo“ und ging.", []),
    ("Er sagte „Hallo (leise", ["„", "("]),
    ("Er sagte „Hallo (leise) und", ["„"]),
    ("Liste [eins, (zwei", ["[", "("]),
    ("Liste [eins, (zwei]", []),          # ] also ends the ( left inside
    ("ein ) zu viel", []),
])
def test_open_marks(before, marks):
    assert cde.open_marks(before) == marks


def test_the_note_shows_while_a_quote_is_open(win):
    win._on_transcript("Er sagte Anführungsstriche Hallo", "text", [])
    assert "„" in win.text()
    note = win._open_marks
    assert note.isVisible()
    assert "Anführungsstriche" in note.text() and "“" in note.text()
    win._on_transcript("Anführungsstriche und ging.", "text", [])
    assert win.text().endswith("„Hallo“ und ging.")
    assert not note.isVisible()


def test_the_innermost_bracket_is_named(win):
    win._edit.setPlainText("Er sagte „Hallo (leise")
    win._edit.moveCursor(win._edit.textCursor().MoveOperation.End)
    note = win._open_marks
    assert note.isVisible()
    assert "Klammer zu" in note.text() and ")" in note.text()
    # the cursor back in front of the quote: nothing is open there
    cur = win._edit.textCursor()
    cur.setPosition(3)
    win._edit.setTextCursor(cur)
    assert not note.isVisible()

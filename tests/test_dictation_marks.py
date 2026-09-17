"""A yellow mark goes when ITS word is dealt with - and not before.

Reported from real use: the yellow tint on words Whisper was unsure about
vanished from the whole text as soon as anything happened - another sentence
dictated, a word marked, a correction made.  Every one of those paths started
by clearing all marks, and the candidate highlights for "nimm N" overwrote
them through the same call.  So correcting one uncertain word erased the
warning on all the others that had not been looked at yet.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import commands_de as cde  # noqa: E402
import dictation_window as dw  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    w = dw.DictationWindow(on_insert=lambda _t: True, on_copy=lambda _t: None)
    yield w
    w.close()


def marked(win):
    """The words currently tinted yellow, in document order."""
    return sorted((c.selectionStart(), w) for c, w in win._low_marks)


def words(win):
    return [w for _pos, w in marked(win)]


def dictate(win, text, unsure=()):
    win._on_transcript(text, "text", list(unsure))


def select(win, word):
    doc = win.text()
    start = doc.index(word)
    cur = win._edit.textCursor()
    cur.setPosition(start)
    cur.setPosition(start + len(word), QTextCursor.MoveMode.KeepAnchor)
    win._edit.setTextCursor(cur)


# -- the reported cases ------------------------------------------------------

def test_dictating_more_keeps_the_earlier_marks(win):
    dictate(win, "Ich habe den Bericht gestern geschickt.", ["Bericht"])
    dictate(win, "Morgen kommt die Antwort.", ["Antwort"])
    assert words(win) == ["Bericht", "Antwort"]
    assert len(win._edit.extraSelections()) == 2, "both have to be painted"


def test_correcting_one_word_only_removes_that_mark(win):
    dictate(win, "Ich habe den Bericht gestern geschickt.", ["Bericht"])
    dictate(win, "Morgen kommt die Antwort.", ["Antwort"])
    select(win, "Bericht")
    win._editor.insert_dictation("Brief")
    assert words(win) == ["Antwort"]


def test_a_command_leaves_the_marks_alone(win):
    dictate(win, "Ich habe den Bericht gestern geschickt.", ["Bericht"])
    assert cde.parse("ans Ende") is not None, "the test needs a real command"
    win._on_transcript("ans Ende", "auto", [])
    assert words(win) == ["Bericht"]


def test_marking_candidates_does_not_erase_the_yellow(win):
    dictate(win, "Der Bericht und der Brief sind da.", ["Bericht"])
    doc = win.text()
    win._mark_candidates([(doc.index("der Brief"), doc.index("der Brief") + 3)])
    assert words(win) == ["Bericht"]
    assert len(win._edit.extraSelections()) == 2   # yellow + green together
    win._cancel_candidates()
    assert words(win) == ["Bericht"]
    assert len(win._edit.extraSelections()) == 1


# -- what DOES end a mark ----------------------------------------------------

def test_extending_the_word_ends_its_mark(win):
    """Measured: a cursor grows with text typed right after its word, so
    "Antwort" -> "Antworten" would stay tinted otherwise - over a word that
    has been edited and is no longer the one Whisper doubted."""
    dictate(win, "Morgen kommt die Antwort.", ["Antwort"])
    cur = win._edit.textCursor()
    cur.setPosition(win.text().index("Antwort") + len("Antwort"))
    cur.insertText("en")
    assert words(win) == []


def test_text_before_a_mark_moves_it_along(win):
    dictate(win, "Morgen kommt die Antwort.", ["Antwort"])
    cur = win._edit.textCursor()
    cur.setPosition(0)
    cur.insertText("Also: ")
    assert words(win) == ["Antwort"]
    pos = marked(win)[0][0]
    assert win.text()[pos:pos + len("Antwort")] == "Antwort"


def test_clearing_the_text_clears_the_marks(win):
    dictate(win, "Morgen kommt die Antwort.", ["Antwort"])
    win._clear_buffer()
    assert words(win) == []
    assert win._edit.extraSelections() == []

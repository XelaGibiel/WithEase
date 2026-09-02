"""Tests for the dictation editor actions (examples/dictation)."""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

import commands_de as cde          # noqa: E402
import editor_actions as ea        # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make(app, text="", cursor_at=None):
    te = QPlainTextEdit()
    te.setPlainText(text)
    ed = ea.Editor(te)
    ed._set_cursor(len(text) if cursor_at is None else cursor_at)
    return ed


def run(ed, transcript):
    """Route an utterance like the engine will: command or dictation."""
    cmd = cde.parse(transcript)
    return ed.apply(cmd) if cmd else ed.insert_dictation(transcript)


def sel(ed):
    return ed.te.textCursor().selectedText()


def pos(ed):
    return ed.te.textCursor().position()


# --- dictation & smart spacing ---------------------------------------------

def test_dictation_inserts_with_spacing(app):
    ed = make(app, "Hallo")
    run(ed, "Welt")
    assert ed.te.toPlainText() == "Hallo Welt"


# --- navigation ------------------------------------------------------------

def test_cursor_before_word(app):
    ed = make(app, "Ein Haus")
    run(ed, "Cursor Haus")
    assert pos(ed) == 4  # right before 'Haus'


def test_cursor_vor_word(app):
    ed = make(app, "Ein schönes Haus")
    run(ed, "Cursor vor Haus")
    assert pos(ed) == 12  # right before 'Haus'


def test_cursor_vor_phrase(app):
    # multi-word target ("das entsprechende Wort ODER der Text")
    ed = make(app, "Am Anfang steht ein schönes Haus")
    run(ed, "Cursor vor schönes Haus")
    assert pos(ed) == 20  # right before 'schönes Haus'


def test_select_phrase(app):
    ed = make(app, "Bitte das kleine rote Auto nehmen")
    run(ed, "markiere kleine rote Auto")
    assert sel(ed) == "kleine rote Auto"


def test_correction_strips_appended_period(app):
    # Whisper appends "." to the short correction word; it must not land
    # in the middle of the sentence.
    ed = make(app, "Hallo Welt")
    run(ed, "markiere Welt")
    run(ed, "Erde.")
    assert ed.te.toPlainText() == "Hallo Erde"


def test_goto_start_end(app):
    ed = make(app, "abc def")
    run(ed, "an den Anfang")
    assert pos(ed) == 0
    run(ed, "ans Ende")
    assert pos(ed) == 7


# --- selection incl. ambiguity, ranges, blocks -----------------------------

def test_select_word(app):
    ed = make(app, "Das schöne Haus")
    run(ed, "markiere Haus")
    assert sel(ed) == "Haus"


def test_ambiguous_then_pick(app):
    ed = make(app, "Das Haus und das Haus", cursor_at=0)
    res = run(ed, "markiere Haus")
    assert res.status == "ambiguous" and res.count == 2
    run(ed, "nimm zwei")
    c = ed.te.textCursor()
    assert sel(ed) == "Haus" and c.selectionStart() == 17


def test_fuzzy_match_survives_mishearing(app):
    ed = make(app, "Das Haus")
    run(ed, "markiere Maus")      # mis-heard 'Haus' as 'Maus'
    assert sel(ed) == "Haus"


def test_select_range(app):
    ed = make(app, "eins zwei drei vier fünf")
    run(ed, "markiere von zwei bis vier")
    assert sel(ed) == "zwei drei vier"


def test_select_paragraph(app):
    ed = make(app, "Absatz eins\n\nAbsatz zwei")
    run(ed, "markiere diesen Absatz")     # cursor is in the last block
    assert sel(ed) == "Absatz zwei"


def test_select_sentence(app):
    ed = make(app, "Hallo Welt. Wie geht es dir?", cursor_at=18)
    run(ed, "markiere diesen Satz")
    assert sel(ed) == "Wie geht es dir?"


# --- deletion --------------------------------------------------------------

def test_delete_word(app):
    ed = make(app, "Das schöne Haus")
    run(ed, "lösche schöne")
    assert ed.te.toPlainText() == "Das  Haus"


def test_delete_last_word(app):
    ed = make(app, "eins zwei drei")
    run(ed, "lösche das")
    assert ed.te.toPlainText().strip() == "eins zwei"


def test_clear(app):
    ed = make(app, "irgendwas")
    run(ed, "alles löschen")
    assert ed.te.toPlainText() == ""


# --- punctuation / formatting ----------------------------------------------

def test_punct_removes_leading_space(app):
    ed = make(app, "Hallo ")
    run(ed, "Punkt")
    assert ed.te.toPlainText() == "Hallo."


def test_newline_and_paragraph(app):
    """A line break starts a new sentence, so the next dictation is
    capitalised – the same rule that keeps „…und dann das war gut." lower case
    while a sentence is still running (see postprocess.join_dictation)."""
    ed = make(app, "a")
    run(ed, "neue Zeile")
    run(ed, "b")
    assert ed.te.toPlainText() == "a\nB"


def test_capitalize_selection(app):
    ed = make(app, "das schöne haus")
    run(ed, "markiere haus")
    run(ed, "großschreiben")
    assert ed.te.toPlainText() == "das schöne Haus"


# --- correction ------------------------------------------------------------

def test_select_then_respeak_replaces(app):
    ed = make(app, "Ich wohne in Leipzig")
    run(ed, "markiere Leipzig")
    run(ed, "Leibig")
    assert ed.te.toPlainText() == "Ich wohne in Leibig"


def test_correct_last(app):
    ed = make(app, "Anfang ")
    run(ed, "Leipzig")                 # dictated (last insert)
    assert ed.te.toPlainText() == "Anfang Leipzig"
    r = run(ed, "korrigiere das")
    assert r.status == "awaiting_dictation"
    run(ed, "Leibig")
    assert ed.te.toPlainText() == "Anfang Leibig"


def test_replace_from_by(app):
    ed = make(app, "Ich mag Katzen")
    run(ed, "ersetze Katzen durch Hunde")
    assert ed.te.toPlainText() == "Ich mag Hunde"


def test_undo(app):
    ed = make(app, "Start")
    run(ed, "Wort")
    assert "Wort" in ed.te.toPlainText()
    run(ed, "rückgängig")
    assert ed.te.toPlainText() == "Start"

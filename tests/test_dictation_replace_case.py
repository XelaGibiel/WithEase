"""Two things a dictated replacement got wrong.

Reported from real use: dictating "Okay, fürs Protokoll ich habe den Nachricht
…", then saying "markiere den Nachricht" and speaking the replacement, put
"Die Nachricht" into the middle of the line - with a capital D.  Whisper
capitalises every utterance as if it were a sentence of its own, and the path
that replaces a selection inserted that verbatim.  The path that appends to
the end had handled this for a long time; the replacement path never did.

And: "Anführungsstriche" was missing from the spoken-punctuation vocabulary.
Only "Anführungszeichen" and "Gänsefüßchen" were listed, so the perfectly
ordinary third word for it produced the words themselves, mid-sentence.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

import commands_de as cde  # noqa: E402
import editor_actions as ea  # noqa: E402
from postprocess import match_case  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def replace_in(app, text, target, spoken):
    """Select ``target`` in ``text`` and speak ``spoken`` over it."""
    te = QPlainTextEdit()
    te.setPlainText(text)
    editor = ea.Editor(te)
    start = text.index(target)
    cur = te.textCursor()
    cur.setPosition(start)
    cur.setPosition(start + len(target), QTextCursor.MoveMode.KeepAnchor)
    te.setTextCursor(cur)
    editor.insert_dictation(spoken)
    return te.toPlainText()


# -- the reported case -------------------------------------------------------

def test_a_replacement_mid_sentence_is_not_capitalised(app):
    got = replace_in(
        app,
        "Okay, fürs Protokoll ich habe den Nachricht heute vor ungefähr "
        "einer halben Stunde abgesendet.",
        "den Nachricht",
        "Die Nachricht.")            # what Whisper hands over
    assert "ich habe die Nachricht heute" in got
    assert "Die Nachricht" not in got


def test_a_replacement_at_the_start_of_a_sentence_keeps_its_capital(app):
    got = replace_in(app, "Der Termin passt nicht. das Haus ist zu klein.",
                     "das Haus", "Die Wohnung.")
    assert "nicht. Die Wohnung ist" in got


def test_a_replacement_at_the_very_beginning_keeps_its_capital(app):
    got = replace_in(app, "den Nachricht ist angekommen.", "den Nachricht",
                     "Die Nachricht.")
    assert got.startswith("Die Nachricht")


def test_a_noun_keeps_its_capital(app):
    """The rule may only lower words German never capitalises mid-sentence -
    lowering a noun would be a different, worse bug."""
    got = replace_in(app, "Wir treffen uns im Büro morgen früh.", "Büro",
                     "Haus.")
    assert "uns im Haus morgen" in got


# -- the rule on its own -----------------------------------------------------

@pytest.mark.parametrize("before, spoken, want", [
    ("ich habe ", "Die Nachricht", "die Nachricht"),
    ("Er sagte, ", "Das war gut", "das war gut"),
    ("Der Satz endet. ", "Die Nachricht", "Die Nachricht"),
    ("Zeile eins\n", "Die Nachricht", "Die Nachricht"),
    ("", "Die Nachricht", "Die Nachricht"),
    ("und dann ", "Haus", "Haus"),               # a noun is never lowered
    ("und dann ", "Nachricht", "Nachricht"),
    ("ich habe ", "die Nachricht", "die Nachricht"),   # already lower
])
def test_the_case_follows_what_stands_before(before, spoken, want):
    assert match_case(before, spoken) == want


def test_appending_still_works_the_way_it_did():
    """join_dictation now shares the rule; its behaviour must not shift."""
    from postprocess import join_dictation
    # Verified against the version before the rule was lifted out.
    assert join_dictation("und dann ", "Das war gut.") == "das war gut."
    assert join_dictation("Fertig. ", "Das war gut.") == "Das war gut."
    assert join_dictation("und dann ", "Haus.") == "Haus."
    # …and the separator space when the text before has none.
    assert join_dictation("und dann", "Das war gut.") == " das war gut."


# -- the quotation marks -----------------------------------------------------

@pytest.mark.parametrize("word", [
    "Anführungsstriche", "Anführungsstrich", "Anführungszeichen",
    "Gänsefüßchen", "anfuehrungsstriche",
])
def test_every_word_for_a_quotation_mark_works_inline(word):
    got = cde.apply_inline_punctuation(
        f"Ich habe {word} unten die Nachricht {word} oben gesehen.")
    assert got == "Ich habe „die Nachricht“ gesehen."


@pytest.mark.parametrize("phrase, char", [
    ("Anführungsstriche unten", "„"),
    ("Anführungsstriche oben", "“"),
    ("Anführungsstriche auf", "„"),
    ("Anführungsstriche zu", "“"),
    ("Anführungsstrich unten", "„"),
])
def test_a_quotation_mark_spoken_on_its_own(phrase, char):
    cmd = cde.parse(phrase)
    assert cmd is not None, f"{phrase!r} is not understood at all"
    assert cmd.kind == "punct" and cmd.data["char"] == char


def test_the_command_reference_names_the_quotation_marks():
    """He had to guess the word - the list did not mention quotes at all."""
    entries = [cmd for _group, items in cde.CHEAT_SHEET for cmd, _desc in items]
    assert any("Anführungsstriche" in e for e in entries)


def test_ordinary_words_are_still_left_alone():
    for text in ("Der Strich unten war zu kurz.",
                 "Er hat unten gewartet.",
                 "Die Klammer hält gut."):
        assert cde.apply_inline_punctuation(text) == text

"""Tests for the German dictation command parser (examples/dictation)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

import commands_de as cde  # noqa: E402


def k(transcript):
    """Kind of the parsed command, or None for dictation."""
    cmd = cde.parse(transcript)
    return cmd.kind if cmd else None


# --- dictation is never mistaken for a command -----------------------------

@pytest.mark.parametrize("text", [
    "Sehr geehrte Damen und Herren",
    "Ich gehe morgen zum Anfang der Straße",       # contains 'anfang'
    "Das Haus ist schön",
    "Wir markieren das im Kalender als wichtig",   # contains 'markieren'
    "bitte einen Punkt machen wir später",         # not a lone 'punkt'
])
def test_plain_dictation_returns_none(text):
    assert cde.parse(text) is None


# --- navigation ------------------------------------------------------------

def test_cursor_before_word():
    cmd = cde.parse("Cursor Haus")
    assert cmd.kind == "cursor_before" and cmd.data["word"] == "haus"


def test_cursor_vor_and_hinter():
    assert cde.parse("Cursor vor Haus").kind == "cursor_before"
    c = cde.parse("hinter Haus")
    assert c.kind == "cursor_after" and c.data["word"] == "haus"


def test_homophone_kursor():
    assert cde.parse("Kursor Haus.").kind == "cursor_before"


def test_goto_positions():
    assert k("an den Anfang") == "goto_start"
    assert k("ans Ende") == "goto_end"
    assert k("Zeilenanfang") == "line_start"


# --- selection incl. ranges ------------------------------------------------

def test_select_word():
    c = cde.parse("markiere Haus")
    assert c.kind == "select_word" and c.data["word"] == "haus"


def test_select_all():
    assert k("markiere alles") == "select_all"


def test_select_range():
    c = cde.parse("markiere von Haus bis Garten")
    assert c.kind == "select_range"
    assert c.data["from"] == "haus" and c.data["to"] == "garten"


def test_select_sentence_and_paragraph():
    assert cde.parse("markiere diesen Satz").data["which"] == "current"
    assert cde.parse("markiere den letzten Satz").data["which"] == "last"
    assert k("markiere diesen Absatz") == "select_paragraph"


def test_select_last_n_words():
    c = cde.parse("markiere die letzten drei Wörter")
    assert c.kind == "select_last_words" and c.data["n"] == 3


# --- deletion incl. ranges -------------------------------------------------

def test_delete_selection_and_word():
    assert cde.parse("lösche das").data["target"] == "selection_or_last"
    c = cde.parse("lösche Haus")
    assert c.kind == "delete" and c.data["word"] == "haus"


def test_delete_all_and_range_and_paragraph():
    assert k("lösche alles") == "clear"
    assert k("lösche von Haus bis Garten") == "delete_range"
    assert k("lösche diesen Absatz") == "delete_paragraph"


# --- punctuation / formatting ----------------------------------------------

def test_punctuation_words():
    assert cde.parse("Punkt").data["char"] == "."
    assert cde.parse("Komma").data["char"] == ","
    assert cde.parse("Fragezeichen").data["char"] == "?"


def test_newline_and_paragraph():
    assert k("neue Zeile") == "newline"
    assert k("neuer Absatz") == "paragraph"


def test_capitalize():
    assert cde.parse("großschreiben").data["mode"] == "upper"
    assert cde.parse("kleinschreiben").data["mode"] == "lower"


# --- correction / mode / window / pick -------------------------------------

def test_correction_commands():
    assert k("korrigiere das") == "correct_last"
    assert cde.parse("korrigiere Haus").data["word"] == "haus"
    r = cde.parse("ersetze Haus durch Garten")
    assert r.kind == "replace" and r.data["from"] == "Haus" and r.data["to"] == "Garten"
    assert k("rückgängig") == "undo"


def test_spell_and_literal():
    assert k("buchstabieren") == "spell_mode"
    lit = cde.parse("wörtlich neue Zeile")
    assert lit.kind == "literal" and lit.data["text"] == "neue Zeile"


def test_window_commands():
    assert k("einfügen") == "insert"
    assert k("kopieren") == "copy"
    assert k("schließen") == "close"


def test_pick_number():
    assert cde.parse("nimm zwei").data["n"] == 2
    assert cde.parse("nimm 3").data["n"] == 3

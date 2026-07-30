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


def test_inline_punctuation():
    ap = cde.apply_inline_punctuation
    assert ap("der Preis Doppelpunkt zehn Euro") == "der Preis: zehn Euro"
    assert ap("A Schrägstrich B") == "A/B"
    assert ap("Text Klammer auf Wort Klammer zu weiter") == "Text (Wort) weiter"
    assert ap("das ist gut Ausrufezeichen") == "das ist gut!"
    assert ap("Frage Fragezeichen") == "Frage?"
    # "Punkt"/"Komma" stay words (Whisper auto-punctuates; too ambiguous)
    assert ap("Das ist ein Punkt am Horizont") == "Das ist ein Punkt am Horizont"


def test_inline_punctuation_absorbs_whisper_periods():
    ap = cde.apply_inline_punctuation
    # Whisper wraps the spoken symbol as its own sentence – no stray dots
    assert ap("Wort. Fragezeichen.") == "Wort?"
    assert ap("Wort. Fragezeichen. Weiter") == "Wort? Weiter"
    assert ap("Alles klar. Ausrufezeichen.") == "Alles klar!"
    # a real trailing word keeps its space
    assert ap("Frage Fragezeichen und Antwort") == "Frage? und Antwort"


def test_inline_quotes_oben_unten():
    ap = cde.apply_inline_punctuation
    assert ap("Zitat Anführungszeichen unten Hallo Anführungszeichen oben Ende") \
        == "Zitat „Hallo“ Ende"


def test_pick_with_filler_words():
    assert cde.parse("nimm mal eins").data["n"] == 1
    assert cde.parse("nimm die zwei").data["n"] == 2
    assert cde.parse("nimm nummer drei").data["n"] == 3


def test_command_aliases():
    assert k("Text einfügen") == "insert"
    assert k("Text kopieren") == "copy"
    assert k("Fenster zu") == "close"
    assert k("An zu Ende") == "goto_end"
    assert k("an das Ende") == "goto_end"
    assert k("schreib groß") == "capitalize"
    assert cde.parse("schreib groß").data["mode"] == "upper"


def test_inline_spelling():
    cmd = cde.parse("buchstabiere Ludwig Emil Ida")
    assert cmd.kind == "spell_inline"
    assert cde.spell_to_text(cmd.data["text"]) == "Lei"
    # bare word still enters spell mode for a following utterance
    assert k("buchstabieren") == "spell_mode"


def test_cursor_homophones_are_mapped():
    # Whisper mis-hearings of "Cursor" still resolve to the cursor command.
    for said in ("Kaser vor Haus", "Körzer vor Haus", "Curser vor Haus"):
        cmd = cde.parse(said)
        assert cmd is not None and cmd.kind == "cursor_before", said
        assert cmd.data["word"] == "haus"


# --- Whisper auto-punctuation must not break command matching --------------
# Whisper turns short commands into "little sentences" with inner commas and a
# trailing period; these must still be recognised as commands, not dictation.

def test_whisper_punctuation_is_ignored():
    assert k("Markiere, Welt.") == "select_word"
    assert cde.parse("Markiere, Welt.").data["word"] == "welt"
    assert k("Cursor, Haus.") == "cursor_before"
    assert k("An den Anfang.") == "goto_start"
    assert k("Ans Ende!") == "goto_end"
    assert k("Neue Zeile.") == "newline"
    assert k("Neuer Absatz.") == "paragraph"
    assert k("Lösche das.") == "delete"
    assert k("Alles löschen.") == "clear"
    assert k("Einfügen.") == "insert"
    assert k("Kopieren.") == "copy"
    assert k("Rückgängig.") == "undo"
    assert cde.parse("Ersetze Haus, durch Garten.").data["to"] == "Garten"


def test_whisper_punctuation_still_lets_dictation_through():
    # A real sentence stays dictation even after punctuation is stripped.
    assert cde.parse("Ich gehe zum Anfang der Straße.") is None
    assert cde.parse("Das markieren wir im Kalender.") is None

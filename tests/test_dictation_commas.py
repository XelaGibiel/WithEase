"""The commas Whisper leaves out - and the ones it must never invent.

Measured against large-v3 on six dictated German sentences: of the nine commas
grammar demands, Whisper writes four.  It gets "weil"/"wenn" clauses right and
misses the comma before "aber", the one that opens an extended infinitive, and
both commas around a relative clause.

A comma in the wrong place is worse than a missing one - it changes how a
sentence reads - so the counter-examples below matter more than the repairs.
Every one of them is a sentence where the same word takes NO comma.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from postprocess import fix_commas  # noqa: E402


# -- what Whisper actually got wrong -----------------------------------------

@pytest.mark.parametrize("spoken, want", [
    # The measured failure: no comma before a coordinating "aber".
    ("Ich habe versucht dich zu erreichen aber es ist niemand rangegangen.",
     "Ich habe versucht, dich zu erreichen, aber es ist niemand rangegangen."),
    ("Ich wollte anrufen aber du warst nicht da.",
     "Ich wollte anrufen, aber du warst nicht da."),
    ("Wir fahren nicht nach Hause sondern wir bleiben hier.",
     "Wir fahren nicht nach Hause, sondern wir bleiben hier."),
    # Subordinating conjunctions that cannot be anything else.
    ("Ich komme später weil der Zug Verspätung hat.",
     "Ich komme später, weil der Zug Verspätung hat."),
    ("Sag mir bitte ob du Zeit hast.",
     "Sag mir bitte, ob du Zeit hast."),
    ("Ich glaube dass das so nicht stimmt.",
     "Ich glaube, dass das so nicht stimmt."),
    ("Er kam nicht obwohl er es versprochen hatte.",
     "Er kam nicht, obwohl er es versprochen hatte."),
    # Extended infinitive: something stands between the verb and "zu".
    ("Ich habe vergessen dir Bescheid zu sagen.",
     "Ich habe vergessen, dir Bescheid zu sagen."),
    ("Wir haben beschlossen das Haus zu verkaufen.",
     "Wir haben beschlossen, das Haus zu verkaufen."),
])
def test_the_missing_comma_is_inserted(spoken, want):
    assert fix_commas(spoken) == want


# -- the counter-examples: the same words, no comma --------------------------

@pytest.mark.parametrize("sentence", [
    # "aber" as a flavouring particle, right after a finite auxiliary.
    "Das ist aber schön.",
    "Das ist aber der Hammer.",
    "Das war aber ein Fehler.",
    "Sie hat aber die Tür zugemacht.",
    "Er kann aber nichts dafür.",
    # ... and where no subject follows it at all.
    "Er kommt aber später.",
    "Ich finde das aber gut.",
    # "denn" as a question particle.
    "Was ist denn hier los?",
    "Wo warst du denn?",
    # "damit" as a pronominal adverb ("with that"), not a conjunction.
    "Ich bin damit einverstanden.",
    "Er hat damit nichts zu tun.",
    # A bare infinitive takes no comma.
    "Ich habe versucht zu schlafen.",
    "Wir haben angefangen zu essen.",
    # Sentence-initial conjunctions have nothing to be separated from.
    "Weil es regnet bleiben wir zu Hause.",
    "Aber das war ja klar.",
])
def test_no_comma_is_invented(sentence):
    assert fix_commas(sentence) == sentence


# -- existing punctuation is never doubled or moved --------------------------

def test_a_comma_that_is_already_there_stays_single():
    text = "Ich komme später, weil der Zug Verspätung hat."
    assert fix_commas(text) == text


def test_other_punctuation_blocks_a_second_mark():
    for text in ("Er sagte: dass er kommt.",
                 "Ich warte – aber du kommst nicht.",
                 "Er kam nicht; obwohl er es versprach."):
        assert fix_commas(text) == text


def test_nothing_is_ever_removed():
    text = "Erstens, zweitens, und drittens."
    assert fix_commas(text).count(",") >= text.count(",")


# -- shape of the text survives ----------------------------------------------

def test_paragraph_breaks_survive():
    text = "Ich komme später weil es regnet.\n\nBis dann."
    assert "\n\n" in fix_commas(text)


def test_several_sentences_are_each_handled():
    got = fix_commas("Ich glaube dass es klappt. Ich rufe an weil es eilt.")
    assert got == ("Ich glaube, dass es klappt. "
                   "Ich rufe an, weil es eilt.")


@pytest.mark.parametrize("text", ["", "   ", "Hallo", "Ja.", "\n"])
def test_degenerate_input_is_returned_unchanged(text):
    assert fix_commas(text) == text


# -- the sentences the measurement actually produced -------------------------

def test_the_measured_whisper_output_is_repaired():
    """Verbatim from the A/B run against large-v3."""
    got = fix_commas(
        "Ich habe versucht dich zu erreichen aber es ist niemand rangegangen.")
    assert got == ("Ich habe versucht, dich zu erreichen, "
                   "aber es ist niemand rangegangen.")


def test_the_relative_clause_is_left_alone_on_purpose():
    """Whisper misses this one too, but delimiting a relative clause needs
    real parsing - a wrong guess here would read worse than the gap."""
    text = "Der Termin den wir vereinbart haben passt mir nicht mehr."
    assert fix_commas(text) == text

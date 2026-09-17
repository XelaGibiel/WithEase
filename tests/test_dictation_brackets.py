"""Brackets dictated in the middle of a sentence.

Reported from real use: "… raussuchen, Klammer auf, bei Mediamarkt, Klammer
zu, hat keine Eile …" came out as "raussuchen, (, bei Mediamarkt), hat keine
Eile".  Whisper hears a spoken punctuation word as a clause of its own and
wraps it in commas.  The closing side already swallowed the comma in front of
it; the opening side swallowed nothing, so both commas stayed around the "(".
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

import commands_de as cde  # noqa: E402


def test_the_reported_sentence():
    got = cde.apply_inline_punctuation(
        "Kannst du mir bei Gelegenheit die Rechnung dafür raussuchen, "
        "Klammer auf, bei Mediamarkt, Klammer zu, hat keine Eile, kann auch "
        "heute Abend sein, Doppelpunkt.")
    assert got == ("Kannst du mir bei Gelegenheit die Rechnung dafür "
                   "raussuchen (bei Mediamarkt), hat keine Eile, kann auch "
                   "heute Abend sein:")


@pytest.mark.parametrize("spoken, want", [
    # without Whisper's commas nothing changes
    ("Rechnung raussuchen Klammer auf bei Mediamarkt Klammer zu bitte.",
     "Rechnung raussuchen (bei Mediamarkt) bitte."),
    # a sentence end before the bracket stays
    ("Das ist gut. Klammer auf, meistens, Klammer zu.", "Das ist gut. (meistens)"),
    # at the very start there is no space to keep
    ("Klammer auf. Hallo. Klammer zu.", "(Hallo)"),
    # nested: no space between two openers
    ("Siehe Klammer auf eckige Klammer auf eins eckige Klammer zu Klammer zu.",
     "Siehe ([eins])"),
    # quotation marks are openers too
    ("Er sagte, Anführungszeichen unten, Hallo, Anführungszeichen oben, und ging.",
     "Er sagte „Hallo“, und ging."),
])
def test_commas_around_an_opener_belong_to_the_spoken_word(spoken, want):
    assert cde.apply_inline_punctuation(spoken) == want

"""Tests for transcript post-processing (examples/dictation)."""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

import postprocess as pp  # noqa: E402


def test_strips_youtube_outro_hallucination():
    text = ("Das ist mein richtiger Satz. Das war's für heute. "
            "Bis zum nächsten Mal. Tschüss.")
    assert pp.strip_hallucinations(text) == "Das ist mein richtiger Satz."


def test_strips_subtitle_credits():
    assert pp.strip_hallucinations("Hallo Welt. Untertitel von der ARD.") \
        == "Hallo Welt."
    assert pp.strip_hallucinations("Amara.org") == ""


def test_keeps_legit_farewell_when_standalone():
    # A genuine lone farewell (not after a hallucination) must survive.
    assert pp.strip_hallucinations("Tschüss.") == "Tschüss."
    assert pp.strip_hallucinations("Ich komme später. Bis bald.") \
        == "Ich komme später. Bis bald."


def test_keeps_normal_text():
    text = "Sehr geehrte Damen und Herren, ich schreibe Ihnen heute."
    assert pp.strip_hallucinations(text) == text


def test_guard_rejects_overedit():
    orig = "Das ist ein kurzer Satz."
    # model rewrote far too much (too long) → keep original
    assert pp.guard_cleanup(orig, "x" * 200) == orig
    # model returned almost nothing → keep original
    assert pp.guard_cleanup(orig, "Das") == orig
    # empty → keep original
    assert pp.guard_cleanup(orig, "") == orig


def test_guard_accepts_light_edit_and_strips_quotes():
    orig = "das ist ein satz ohne satzzeichen"
    cleaned = '"Das ist ein Satz ohne Satzzeichen."'
    assert pp.guard_cleanup(orig, cleaned) == "Das ist ein Satz ohne Satzzeichen."


def test_strip_repetitions_collapses_sentence_loop():
    # Whisper repetition-loop hallucination → collapse to one occurrence.
    assert pp.strip_repetitions("Und so. Und so. Und so ist es nicht.") \
        == "Und so. Und so ist es nicht."
    assert pp.strip_repetitions("Das ist gut. Das ist gut. Das ist gut.") \
        == "Das ist gut."


def test_strip_repetitions_collapses_word_loop():
    assert pp.strip_repetitions("ja ja ja ja das war es") == "ja das war es"


def test_strip_repetitions_keeps_genuine_doubles_and_normal_text():
    assert pp.strip_repetitions("sehr, sehr gut") == "sehr, sehr gut"
    assert pp.strip_repetitions("Hallo Welt.") == "Hallo Welt."
    assert pp.strip_repetitions("") == ""

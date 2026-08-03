"""Tests for vocabulary learning + spoken→written forms (examples/dictation)."""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

import vocabulary as vocab  # noqa: E402


def test_extract_terms_finds_names_and_terms():
    text = ("Herr Leibig arbeitet mit WithEase am Diktierfenster. "
            "Das ist ein normaler Satz mit gewöhnlichen Wörtern.")
    terms = vocab.extract_terms(text)
    lower = [t.lower() for t in terms]
    assert "Leibig" in terms
    assert "WithEase" in terms
    assert "Diktierfenster" in terms
    # common words / nouns are not proposed
    assert "das" not in lower          # lower-case common word
    assert "satz" not in lower         # common noun (capitalised in German)
    assert "arbeitet" not in lower     # lower-case verb


def test_extract_terms_deduplicates_and_ranks():
    text = "Xanthos Xanthos Xanthos Kurzwort"
    terms = vocab.extract_terms(text)
    assert terms and terms[0] == "Xanthos"      # most frequent first
    assert terms.count("Xanthos") == 1          # de-duplicated


def test_apply_spoken_forms_single_and_phrase():
    pairs = [("with ease", "WithEase"), ("firma", "Müller GmbH")]
    assert vocab.apply_spoken_forms("Ich nutze with ease täglich",
                                    pairs) == "Ich nutze WithEase täglich"
    assert vocab.apply_spoken_forms("meine firma ist gut",
                                    pairs) == "meine Müller GmbH ist gut"


def test_apply_spoken_forms_case_insensitive_whole_word():
    pairs = [("kaus", "Haus")]
    assert vocab.apply_spoken_forms("Kaus und Pause", pairs) == "Haus und Pause"
    # substring must not be replaced
    assert vocab.apply_spoken_forms("Pausenraum", pairs) == "Pausenraum"


def test_apply_spoken_forms_prefers_longer_match():
    pairs = [("ease", "X"), ("with ease", "WithEase")]
    assert vocab.apply_spoken_forms("with ease", pairs) == "WithEase"


def test_extract_scored_filters_common_words_and_scores_strength():
    letter = ("Sehr geehrte Damen und Herren, in Ihrem Schreiben vom Juli. "
              "Herr Leibig nutzt WithEase und die GmbH am ABS-System 2.0.")
    scored = vocab.extract_terms_scored(letter)
    strong = {t for t, s in scored if s}
    weak = {t for t, s in scored if not s}
    # CamelCase / acronym / digit-mix are confident (pre-checked)
    assert {"WithEase", "GmbH", "ABS-System"} <= strong
    # a plain name is a weak candidate (offered, not pre-checked)
    assert "Leibig" in weak
    # common German words / formal address never become terms
    allt = set(vocab.extract_terms(letter))
    for junk in ("Damen", "Herren", "Ihrem", "Juli", "Sehr", "Schreiben"):
        assert junk not in strong
    assert not ({"Damen", "Herren", "Ihrem", "Juli"} & allt)

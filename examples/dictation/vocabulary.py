"""Vocabulary helpers: learn terms from a document, and a spoken→written map.

Pure, Qt-free logic so it is easy to unit-test.

* :func:`extract_terms` — pull likely names/technical terms out of a text the
  user provides (Dragon's "Learn from a document"), to seed the glossary.
* :func:`apply_spoken_forms` — replace a user-defined *spoken form* with its
  *written form* ("with ease" → "WithEase"), whole word or phrase.
"""
from __future__ import annotations

import re
from collections import Counter

# Frequent German words that are never useful as custom vocabulary (lower-case).
_COMMON = {
    "der", "die", "das", "und", "oder", "aber", "denn", "weil", "dass", "ist",
    "sind", "war", "waren", "wird", "werden", "wurde", "hat", "haben", "hatte",
    "kann", "können", "muss", "müssen", "soll", "sollen", "will", "wollen",
    "ich", "du", "er", "sie", "es", "wir", "ihr", "mich", "dich", "sich", "uns",
    "ein", "eine", "einen", "einem", "einer", "eines", "kein", "keine",
    "nicht", "auch", "noch", "schon", "nur", "sehr", "mehr", "wenn", "als",
    "wie", "was", "wer", "wo", "wann", "warum", "hier", "dort", "dann", "also",
    "mit", "ohne", "für", "von", "vom", "zum", "zur", "auf", "aus", "bei",
    "nach", "über", "unter", "vor", "durch", "gegen", "um", "an", "in", "im",
    "zu", "am", "des", "dem", "den", "im", "man", "mal", "so", "im", "ja",
    "nein", "bitte", "danke", "guten", "sehr", "diese", "dieser", "dieses",
    "the", "and", "for", "with", "that", "this", "you", "are", "was", "have",
    # frequent German nouns (all German nouns are capitalised, so these would
    # otherwise be proposed as "terms")
    "satz", "sätze", "wort", "worte", "wörter", "wörtern", "tag", "tage",
    "jahr", "jahre", "zeit", "mann", "frau", "kind", "kinder", "leute", "welt",
    "haus", "häuser", "auto", "stadt", "land", "hand", "kopf", "seite", "teil",
    "weg", "arbeit", "beispiel", "frage", "antwort", "problem", "grund", "fall",
    "name", "text", "sache", "ding", "dinge", "person", "leben", "morgen",
    "abend", "woche", "monat", "stunde", "minute", "herr", "herrn", "dame",
    # formal address / letters (a common source of junk terms)
    "sie", "ihr", "ihre", "ihrem", "ihren", "ihrer", "ihres", "ihnen",
    "sehr", "geehrte", "geehrter", "geehrten", "damen", "herren", "liebe",
    "lieber", "freundlichen", "freundliche", "grüßen", "gruß", "grüße",
    "hochachtungsvoll", "anrede", "betreff", "datum", "unterschrift",
    "vorname", "vornamen", "nachname", "nachnamen", "adresse", "straße",
    "nummer", "telefon", "email", "e-mail",
    # months + weekdays
    "januar", "februar", "märz", "april", "mai", "juni", "juli", "august",
    "september", "oktober", "november", "dezember", "montag", "dienstag",
    "mittwoch", "donnerstag", "freitag", "samstag", "sonntag",
    # more frequent nouns / capitalised function words that add only noise
    "sicht", "lücke", "beitrag", "beiträge", "nachweis", "nachweise",
    "grund", "gründe", "bereich", "bereiche", "punkt", "punkte", "stelle",
    "stellen", "höhe", "art", "form", "wert", "werte", "zahl", "zahlen",
    "betrag", "beträge", "summe", "anzahl", "menge", "möglichkeit", "recht",
    "gesetz", "regel", "regeln", "seite", "seiten", "ende", "anfang",
    "beginn", "schluss", "ziel", "ziele", "zweck", "sinn", "wille",
    "familie", "eltern", "mutter", "vater", "sohn", "tochter", "bruder",
    "schwester", "freund", "freunde", "gruppe", "team", "firma", "büro",
    "geld", "euro", "cent", "konto", "rechnung", "vertrag", "antrag",
    "antragsteller", "kunde", "kunden", "bürger", "mensch", "menschen",
    "wochen", "monate", "jahre", "tagen", "uhr", "termin", "termine",
}


def _term_strength(word: str) -> str | None:
    """Classify a token as a vocabulary candidate.

    Returns ``"strong"`` (almost certainly a name/coinage worth learning),
    ``"weak"`` (a plain capitalised word – might be a name, but in German most
    capitalised words are ordinary nouns Whisper already knows), or ``None``
    (not a candidate).  German capitalises *all* nouns, so "is capitalised" is a
    weak signal by itself – strong signals are CamelCase, ALL-CAPS acronyms and
    letter+digit mixes."""
    if len(word) < 3 or word.lower() in _COMMON:
        return None
    has_digit = any(c.isdigit() for c in word)
    internal_upper = any(c.isupper() for c in word[1:])   # CamelCase / ALL-CAPS
    if internal_upper or has_digit:
        return "strong"
    if word[0].isupper():
        return "weak"
    return None


def extract_terms_scored(text: str, limit: int = 100) -> list[tuple[str, bool]]:
    """Like :func:`extract_terms` but each term carries ``is_strong`` so the UI
    can pre-check only the confident ones.  Strong terms first, then by
    frequency, then length."""
    if not text:
        return []
    tokens = re.findall(r"[^\W\d_][\w'’-]*", text, re.UNICODE)
    counts: Counter[str] = Counter()
    best_form: dict[str, str] = {}
    strong: dict[str, bool] = {}
    for w in tokens:
        kind = _term_strength(w)
        if kind is None:
            continue
        key = w.casefold()
        counts[key] += 1
        strong[key] = strong.get(key, False) or kind == "strong"
        internal_upper = any(c.isupper() for c in w[1:])
        if key not in best_form or (internal_upper and not any(
                c.isupper() for c in best_form[key][1:])):
            best_form[key] = w
    ranked = sorted(counts.items(),
                    key=lambda kv: (not strong[kv[0]], -kv[1], -len(kv[0])))
    return [(best_form[key], strong[key]) for key, _n in ranked[:limit]]


def extract_terms(text: str, limit: int = 100) -> list[str]:
    """Return likely custom-vocabulary terms from ``text`` – ranked, de-duped."""
    return [t for t, _strong in extract_terms_scored(text, limit)]


def _sub_phrase(text: str, spoken: str, written: str) -> str:
    words = spoken.split()
    if not words:
        return text
    pattern = r"\b" + r"\s+".join(re.escape(w) for w in words) + r"\b"
    return re.sub(pattern, lambda _m: written, text, flags=re.IGNORECASE)


def apply_spoken_forms(text: str, pairs: list[tuple[str, str]]) -> str:
    """Replace each spoken form with its written form (case-insensitive, whole
    word/phrase).  Longer spoken forms are matched first."""
    if not text or not pairs:
        return text
    result = text
    for spoken, written in sorted(pairs, key=lambda p: -len(p[0] or "")):
        spoken = (spoken or "").strip()
        written = (written or "").strip()
        if spoken and written:
            result = _sub_phrase(result, spoken, written)
    return result

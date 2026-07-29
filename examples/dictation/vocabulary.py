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
}


def extract_terms(text: str, limit: int = 100) -> list[str]:
    """Return likely custom-vocabulary terms from ``text``: proper nouns,
    CamelCase, and long/uncommon words – ranked by frequency, de-duplicated."""
    if not text:
        return []
    tokens = re.findall(r"[^\W\d_][\w'’-]*", text, re.UNICODE)
    counts: Counter[str] = Counter()
    best_form: dict[str, str] = {}
    for w in tokens:
        if len(w) < 3:
            continue
        low = w.lower()
        if low in _COMMON:
            continue
        capitalised = w[0].isupper()
        camel = any(c.isupper() for c in w[1:])
        # CamelCase is a strong term signal; otherwise a capitalised word that
        # is not a common noun.  (Plain long lower-case words are usually not
        # names/terms and would only add noise.)
        if not (camel or (capitalised and low not in _COMMON)):
            continue
        key = w.casefold()
        counts[key] += 1
        # Keep the form with an internal capital (CamelCase) if seen, else first.
        if key not in best_form or (camel and not any(
                c.isupper() for c in best_form[key][1:])):
            best_form[key] = w
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    return [best_form[key] for key, _n in ranked[:limit]]


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

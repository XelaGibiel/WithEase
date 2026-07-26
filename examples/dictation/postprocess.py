"""Transcript post-processing: hallucination filter + optional AI cleanup.

Whisper tends to *invent* text on silence – classic YouTube-outro or subtitle
phrases like "Das war's für heute. Bis zum nächsten Mal. Tschüss." or
"Untertitel von …", none of which the user ever said.  :func:`strip_hallucinations`
removes those locally, no AI required.

:func:`build_cleanup_prompt` / :func:`guard_cleanup` support the *optional* AI
"make it read well" pass (local or cloud); the actual model call lives in the
module.  ``guard_cleanup`` keeps the model from silently changing the meaning.
"""
from __future__ import annotations

import re
import unicodedata

# High-precision signatures of Whisper hallucinations (matched case-insensitively
# against a single sentence).  These are essentially never real dictation.
_HALLUCINATION_RE = [
    re.compile(p) for p in (
        r"das war'?s für heute",
        r"bis zum nächsten mal",
        r"vielen dank fürs? zuschauen",
        r"danke fürs? zuschauen",
        r"untertitel(ung)?( von| im auftrag| des| der)",
        r"amara\.org",
        r"abonn(iert|ier|ieren)",
        r"copyright\b",
        r"\bthanks? for watching\b",
        r"\bplease subscribe\b",
        r"\bsubscribe to\b",
        r"untertitel im auftrag",
    )
]

# Trailing farewells that are only dropped when they directly follow a detected
# hallucination (so a genuine lone "Tschüss" is never removed).
_FAREWELLS = {
    "tschuss", "ciao", "tschau", "auf wiedersehen", "bis dann", "bis bald",
    "machts gut", "man sieht sich",
}


def _fold(text: str) -> str:
    text = text.lower().replace("ß", "ss")
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _is_hallucination(sentence: str) -> bool:
    s = sentence.strip().lower()
    return any(rx.search(s) for rx in _HALLUCINATION_RE)


def _is_farewell(sentence: str) -> bool:
    s = _fold(sentence).strip(" .,;:!?…")
    return s in _FAREWELLS


def strip_hallucinations(text: str) -> str:
    """Remove invented outro / subtitle sentences from a transcript."""
    if not text or not text.strip():
        return ""
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    kept: list[str] = []
    prev_removed = False
    for part in parts:
        if not part.strip():
            continue
        if _is_hallucination(part):
            prev_removed = True
            continue
        if prev_removed and _is_farewell(part):
            # e.g. a lone "Tschüss." right after "Das war's für heute."
            continue
        prev_removed = False
        kept.append(part.strip())
    return " ".join(kept).strip()


# ---------------------------------------------------------------------------
# Optional AI cleanup (local or cloud) – prompt + result guard
# ---------------------------------------------------------------------------

def build_cleanup_prompt() -> str:
    """System instruction for the gentle 'make it read well' pass."""
    return (
        "Du bist ein Korrektor für diktierten deutschen Text. Korrigiere nur "
        "Rechtschreibung, Grammatik und Zeichensetzung. Ändere die Bedeutung "
        "NICHT, füge nichts hinzu und lasse nichts weg. Gib ausschließlich den "
        "korrigierten Text zurück – ohne Anführungszeichen, ohne Kommentare."
    )


def guard_cleanup(original: str, cleaned: str) -> str:
    """Accept the AI result only if it looks like a light edit, else keep the
    original – so an over-eager model can never rewrite or drop content."""
    if not cleaned:
        return original
    c = cleaned.strip().strip("\"'„“”").strip()
    if not c:
        return original
    o = (original or "").strip()
    if o and (len(c) < len(o) * 0.5 or len(c) > len(o) * 1.8):
        return original       # too much changed → distrust it
    return c

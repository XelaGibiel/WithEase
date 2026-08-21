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


# Words that are never doubled on purpose, so an immediate "X X" is a
# recognition glitch ("in in", "und und").  Only prepositions and conjunctions:
# articles/pronouns are excluded because "die die dort stehen" / "der der da
# steht" are valid relative clauses, and emphasis adverbs ("sehr sehr") double
# legitimately too.
_NEVER_DOUBLED = frozenset((
    # prepositions
    "in an auf aus bei mit nach seit von zu über unter vor durch für gegen "
    "ohne um im am ins vom zum zur gegenüber "
    # conjunctions
    "und oder aber denn sondern").split())


def strip_repetitions(text: str) -> str:
    """Collapse Whisper's repetition-loop hallucinations, where a short phrase
    is repeated over and over ("Und so. Und so. Und so.") – something a person
    never actually dictates.  A phrase repeated 3+ times in a row is reduced to
    a single occurrence; genuine doubles ("sehr, sehr") are left alone."""
    if not text or not text.strip():
        return ""
    # 1) sentence-level: drop consecutive duplicate sentences (keep the first).
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    kept: list[str] = []
    for part in parts:
        norm = _fold(part).strip(" .,;:!?…")
        if not norm:
            continue
        if kept and _fold(kept[-1]).strip(" .,;:!?…") == norm:
            continue                # same sentence again → skip
        kept.append(part.strip())
    text = " ".join(kept).strip()
    # 2) word/phrase-level. Single-word loops first, so the multi-word rule
    #    below can't mis-read "ja ja ja ja" as "ja ja" repeated.
    # a) a single word repeated 3+ times ("ja ja ja ja" → "ja"); genuine
    #    emphasis doubles ("sehr sehr") are deliberately kept.
    text = re.sub(r"\b(\w+)(?:\W+\1\b){2,}", lambda m: m.group(1), text,
                  flags=re.IGNORECASE)
    # b) an immediate double of a never-doubled function word ("in in" → "in").
    _SEP = r"[ \t,;:/–-]+"        # separators that stay within one sentence
    text = re.sub(r"\b(" + "|".join(_NEVER_DOUBLED) + rf"){_SEP}\1\b",
                  lambda m: m.group(1), text, flags=re.IGNORECASE)
    # c) a multi-word group (2–4 words) repeated within a sentence
    #    ("Karte Whisper Karte Whisper" → "Karte Whisper"); the separators
    #    exclude "." "!" "?" so it never merges across two sentences.
    text = re.sub(rf"\b(\w+(?:{_SEP}\w+){{1,3}})(?:{_SEP}\1\b)+",
                  lambda m: m.group(1), text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip()


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


# German yes/no questions put a finite verb first (V1 word order): „Können Sie
# …", „Ist das …", „Hast du …".  Whisper often ends such polite questions with a
# period; this restores the question mark.  Kept to modal + sein/haben/werden
# forms so it is high-precision (declaratives are V2, so a leading modal is a
# strong question signal).
_Q_OPENERS = frozenset((
    "kann kannst können könnt könnte könntest könnten könntet "
    "würde würdest würden würdet "
    "darf darfst dürfen dürft dürfte dürftest dürften dürftet "
    "hab habe hast hat habt haben hätte hättest hätten hättet "
    "bin bist ist sind seid wäre wärst wären wärt war warst waren wart "
    "soll sollst sollen sollt sollte solltest sollten solltet "
    "will willst wollen wollt wollte wolltest wollten wolltet "
    "muss musst müssen müsst müsste müsstest müssten müsstet "
    "mag magst mögen mögt möchte möchtest möchten möchtet "
    "wird wirst werden werdet weiß weißt wisst wissen").split())


def fix_question_marks(text: str) -> str:
    """Give a period-ended sentence a question mark when it clearly opens a
    yes/no question (a finite modal/auxiliary verb in first position).
    Conservative: only these openers, only a trailing period, never touches
    „!" or an existing „?"."""
    if not text or not text.strip():
        return text
    # Split into sentences but KEEP the separators (capturing group), so any
    # line/paragraph breaks – e.g. in an AI-formatted e-mail – survive instead
    # of being flattened to single spaces.  Odd indices are the whitespace.
    tokens = re.split(r"((?<=[.!?…])\s+)", text)
    for i, tok in enumerate(tokens):
        if i % 2 == 1 or not tok.strip():        # separator → keep verbatim
            continue
        core = tok.rstrip()
        first = re.split(r"[\s,]", core.strip(), 1)[0].lower().strip(".,!?…")
        if core.endswith(".") and not core.endswith("..") and first in _Q_OPENERS:
            tokens[i] = core[:-1] + "?" + tok[len(core):]   # keep trailing ws
    return "".join(tokens)


# Words German only ever capitalises at the very start of a sentence – never as
# a noun, and never the formal „Sie"/„Ihr" (those are deliberately kept out).
# Used to undo Whisper's occasional mid-sentence over-capitalisation ("Ich gehe
# Nach Hause") without ever touching a real (always-capitalised) German noun.
_LOWER_WORDS = frozenset((
    # articles / determiners
    "der die das den dem des ein eine einen einem einer eines "
    "dieser diese dieses jener jene jenes jeder jede jedes "
    "kein keine keinen keinem keiner welcher welche welches "
    "solcher solche solches mancher manche manches "
    # safe personal pronouns (no sie/Sie, ihr/Ihr, ihnen/Ihnen – ambiguous)
    "ich du er es wir mich dich mir dir ihn ihm uns euch man "
    # prepositions
    "in an auf aus bei mit nach seit von zu zum zur über unter vor "
    "hinter neben zwischen durch für gegen ohne um bis ab am im ins "
    "vom beim gegenüber "
    # conjunctions
    "und oder aber denn sondern weil dass wenn als ob damit obwohl "
    "während bevor nachdem sobald sowie sowohl also dennoch trotzdem "
    # adverbs / particles that are never nouns
    "dann jetzt hier dort schon noch auch nur sehr immer wieder nicht "
    "eigentlich vielleicht wirklich gerne ziemlich sofort bald oft "
    "manchmal nie niemals überhaupt eben"
).split())

# A single Capitalised word, optional leading/trailing quotes+punctuation.
_CAP_WORD = re.compile(
    r"([\"'„»«(\[]*)([A-ZÄÖÜ])([a-zäöüß'’\-]*)([.,;:!?…)\]\"'“”»]*)$")


def fix_casing(text: str) -> str:
    """Undo Whisper's occasional mid-sentence over-capitalisation.

    A word is lower-cased only when (a) it is *not* the first word of its
    sentence and (b) it is a pure function word German never capitalises except
    at a sentence start (see ``_LOWER_WORDS``).  German nouns – always
    capitalised – are therefore never touched, acronyms (all-caps) are left
    alone, and the formal „Sie"/„Ihr" is preserved (kept out of the list)."""
    if not text or not text.strip():
        return text
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    out = []
    for part in parts:
        tokens = part.split(" ")
        for i, tok in enumerate(tokens):
            if i == 0 or not tok:
                continue                       # keep each sentence's first word
            m = _CAP_WORD.match(tok)
            if not m:
                continue                       # not a simple Capitalised word
            lead, head, rest, trail = m.groups()
            if (head + rest).lower() in _LOWER_WORDS:
                tokens[i] = lead + head.lower() + rest + trail
        out.append(" ".join(tokens))
    return " ".join(out)

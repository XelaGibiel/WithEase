"""German voice-command grammar for the dictation window.

Turns a Whisper transcript of ONE utterance into a structured :class:`Command`,
or ``None`` when the utterance is plain dictation text.

Design: a command only fires when the *whole* normalised utterance matches a
pattern (``re.fullmatch``).  Because dictation is push-to-talk (one utterance at
a time), a dictated sentence can never be mistaken for a command.  Other
languages plug in by providing a module with the same ``parse()`` surface.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

LANGUAGE = "de"


@dataclass
class Command:
    """A recognised voice command. ``kind`` selects the editor action; the
    remaining parameters live in ``data``."""
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

# Whisper homophones / spellings that should map onto command keywords.
_HOMOPHONES = {
    "kursor": "cursor",
    "korsor": "cursor",
    "cursa": "cursor",
    "kaser": "cursor",
    "körzer": "cursor",
    "koerzer": "cursor",
    "curser": "cursor",
    "kurser": "cursor",
    "köser": "cursor",
    "markieren": "markiere",
    "markier": "markiere",
    "lösch": "lösche",
    "loesche": "lösche",
    "lesche": "lösche",
    "löshe": "lösche",
    "korrigier": "korrigiere",
}

# Spoken punctuation → the character to insert.
PUNCT_WORDS = {
    "punkt": ".", "komma": ",", "fragezeichen": "?", "ausrufezeichen": "!",
    "doppelpunkt": ":", "semikolon": ";", "strichpunkt": ";",
    "bindestrich": "-", "gedankenstrich": "–", "auslassungspunkte": "…",
}

# German number words → int (for "nimm zwei", "die letzten drei Wörter").
_NUMBERS = {
    "null": 0, "ein": 1, "eins": 1, "eine": 1, "zwei": 2, "drei": 3,
    "vier": 4, "fünf": 5, "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8,
    "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "zwoelf": 12,
}


# German spelling alphabet (Buchstabiertafel) → letter, for the spell mode.
SPELL_ALPHABET = {
    "anton": "A", "ärger": "Ä", "aerger": "Ä", "berta": "B", "cäsar": "C",
    "caesar": "C", "dora": "D", "emil": "E", "friedrich": "F", "gustav": "G",
    "heinrich": "H", "ida": "I", "julius": "J", "kaufmann": "K", "ludwig": "L",
    "martha": "M", "nordpol": "N", "otto": "O", "ökonom": "Ö", "oekonom": "Ö",
    "paula": "P", "quelle": "Q", "richard": "R", "samuel": "S", "siegfried": "S",
    "theodor": "T", "ulrich": "U", "übermut": "Ü", "uebermut": "Ü",
    "viktor": "V", "wilhelm": "W", "xaver": "X", "xanthippe": "X",
    "ypsilon": "Y", "zacharias": "Z", "zeppelin": "Z", "eszett": "ß",
}


def spell_to_text(transcript: str) -> str:
    """Convert a spelled-out utterance to a word.

    Accepts single letters ("h a u s"), the German spelling alphabet
    ("Heinrich Anton Ulrich Samuel") or a mix.  Returns the assembled string
    with only the first letter capitalised (typical for a dictated name)."""
    letters: list[str] = []
    for tok in normalise(transcript).split(" "):
        if not tok:
            continue
        if tok in SPELL_ALPHABET:
            letters.append(SPELL_ALPHABET[tok])
        elif len(tok) == 1 and tok.isalpha():
            letters.append(tok.upper())
    if not letters:
        return ""
    word = "".join(letters)
    return word[0].upper() + word[1:].lower()


# Any punctuation Whisper might sprinkle into a short command utterance.
_PUNCT_CHARS = r"[.,;:!?…·\"'“”„‚‘’»«›‹()\[\]{}/\\–—-]+"


def normalise(text: str) -> str:
    """Lower-case, neutralise *all* punctuation, collapse whitespace and apply
    the homophone map word-by-word.

    Whisper auto-punctuates even one- and two-word commands ("Markiere, Welt."),
    so we replace every punctuation mark – inner ones included – with a space.
    Otherwise a stray comma would stop a command pattern from matching and the
    utterance would be inserted as dictation text instead of being executed."""
    text = unicodedata.normalize("NFC", text or "").strip().lower()
    text = re.sub(_PUNCT_CHARS, " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    words = [_HOMOPHONES.get(w, w) for w in text.split(" ") if w]
    return " ".join(words)


def _num(word: str) -> int | None:
    word = word.strip()
    if word.isdigit():
        return int(word)
    return _NUMBERS.get(word)


def _orig(transcript: str) -> str:
    """Whitespace-normalised transcript with ORIGINAL casing kept (for text
    that gets inserted verbatim, e.g. 'ersetze … durch <Name>')."""
    text = unicodedata.normalize("NFC", transcript or "").strip()
    text = text.strip(" \t\r\n.,;:!?…\"'“”„»«()[]")
    return re.sub(r"\s+", " ", text)


def _clean_target(text: str) -> str:
    """A captured <word>/<phrase> target, trimmed of stray punctuation."""
    return text.strip(" \t.,;:!?…\"'“”„»«()[]")


# ---------------------------------------------------------------------------
# Matchers – ordered; first full-utterance match wins.
# ---------------------------------------------------------------------------

def _m_window(t: str) -> Command | None:
    if t in ("einfügen", "einfuegen", "text einfügen", "text einfuegen",
             "übernehmen", "uebernehmen", "fertig"):
        return Command("insert")
    if t in ("kopieren", "text kopieren", "in die zwischenablage"):
        return Command("copy")
    if t in ("ziel wählen", "ziel waehlen", "ziel merken", "ziel-app wählen",
             "ziel app wählen", "zielanwendung wählen", "app wählen"):
        return Command("reselect_target")
    if t in ("alles löschen", "alles loeschen", "text löschen", "leeren"):
        return Command("clear")
    if t in ("abbrechen", "schließen", "schliessen", "schließe", "schliesse",
             "fenster schließen", "fenster schliessen", "fenster zu",
             "diktierfenster schließen", "diktierfenster schliessen",
             "diktierfenster zu", "diktat schließen", "diktat schliessen"):
        return Command("close")
    return None


def _m_mode(t: str) -> Command | None:
    m = re.fullmatch(r"wörtlich (.+)|woertlich (.+)", t)
    if m:
        return Command("literal", {"text": _clean_target(m.group(1) or m.group(2))})
    # Inline spelling in one breath: "buchstabiere Ludwig Emil Ida …".
    m = re.fullmatch(r"(?:buchstabiere|buchstabiern|buchstabieren) (.+)", t)
    if m:
        return Command("spell_inline", {"text": m.group(1)})
    if t in ("buchstabieren", "buchstabiere", "buchstabiermodus",
             "buchstabier modus"):
        return Command("spell_mode")
    return None


def _m_correct(t: str) -> Command | None:
    if t in ("korrigiere das", "korrigier das", "korrigiere letztes"):
        return Command("correct_last")
    m = re.fullmatch(r"(?:korrigiere|korrigier) (.+)", t)
    if m:
        return Command("correct", {"word": _clean_target(m.group(1))})
    m = re.fullmatch(r"ersetze (.+) durch (.+)", t)
    if m:
        return Command("replace", {"from": _clean_target(m.group(1)),
                                   "to": _clean_target(m.group(2))})
    if t in ("rückgängig", "rueckgaengig", "mach rückgängig",
             "mach das rückgängig"):
        return Command("undo")
    if t in ("wiederholen", "wiederherstellen", "mach wieder"):
        return Command("redo")
    return None


def _m_pick(t: str) -> Command | None:
    # tolerate filler words: "nimm mal eins", "nimm die zwei", "nimm nummer 3"
    m = re.fullmatch(
        r"(?:nimm|nummer|wähle|waehle)(?: mal| bitte| die| den| das| nummer)* (\w+)",
        t)
    if m:
        n = _num(m.group(1))
        if n is not None:
            return Command("pick", {"n": n})
    return None


def _range_or_scope(verb: str, kind_word: str, kind_range: str, t: str):
    """Shared matcher for 'markiere …' / 'lösche …' bodies (t already has the
    verb stripped)."""
    if t in ("alles", "den ganzen text", "gesamten text"):
        return Command("select_all" if verb == "select" else "clear")
    m = re.fullmatch(r"von (.+) bis (.+)", t)
    if m:
        return Command(f"{verb}_range", {"from": _clean_target(m.group(1)),
                                         "to": _clean_target(m.group(2))})
    m = re.fullmatch(r"(?:diesen|den letzten|letzten) satz", t)
    if m:
        which = "last" if "letzten" in t else "current"
        return Command(f"{verb}_sentence", {"which": which})
    m = re.fullmatch(r"(?:diesen|den letzten|letzten) absatz", t)
    if m:
        which = "last" if "letzten" in t else "current"
        return Command(f"{verb}_paragraph", {"which": which})
    m = re.fullmatch(r"(?:die )?letzte zeile|diese zeile", t)
    if m:
        return Command(f"{verb}_line", {})
    m = re.fullmatch(r"(?:die )?letzten (\w+) wörter|(?:die )?letzten (\w+) woerter", t)
    if m:
        n = _num(m.group(1) or m.group(2))
        if n:
            return Command(f"{verb}_last_words", {"n": n})
    if verb == "delete" and t in ("das", "", "letztes wort", "das wort"):
        return Command("delete", {"target": "selection_or_last"})
    return None


def _m_delete(t: str) -> Command | None:
    m = re.fullmatch(r"(?:lösche|loesche|entferne) ?(.*)", t)
    if not m:
        return None
    body = m.group(1).strip()
    scoped = _range_or_scope("delete", "delete", "delete_range", body)
    if scoped is not None:
        return scoped
    if body:
        return Command("delete", {"target": "word", "word": _clean_target(body)})
    return Command("delete", {"target": "selection_or_last"})


def _m_select(t: str) -> Command | None:
    m = re.fullmatch(r"(?:markiere|wähle|waehle|auswählen) (.+)", t)
    if not m:
        return None
    body = m.group(1).strip()
    scoped = _range_or_scope("select", "select", "select_range", body)
    if scoped is not None:
        return scoped
    return Command("select_word", {"word": _clean_target(body)})


def _m_navigate(t: str) -> Command | None:
    if t in ("an den anfang", "zum anfang", "an den start", "zum start",
             "ganz nach oben", "textanfang"):
        return Command("goto_start")
    if t in ("ans ende", "an das ende", "an zu ende", "zum ende",
             "zum schluss", "an den schluss", "ganz nach unten", "textende"):
        return Command("goto_end")
    if t in ("zeilenanfang", "an den zeilenanfang", "zum zeilenanfang"):
        return Command("line_start")
    if t in ("zeilenende", "an das zeilenende", "zum zeilenende"):
        return Command("line_end")
    m = re.fullmatch(r"cursor vor (.+)|vor (.+)", t)
    if m:
        return Command("cursor_before", {"word": _clean_target(m.group(1) or m.group(2))})
    m = re.fullmatch(r"cursor hinter (.+)|hinter (.+)", t)
    if m:
        return Command("cursor_after", {"word": _clean_target(m.group(1) or m.group(2))})
    m = re.fullmatch(r"(?:cursor|gehe zu|springe zu) (.+)", t)
    if m:
        return Command("cursor_before", {"word": _clean_target(m.group(1))})
    return None


def _m_format(t: str) -> Command | None:
    if t in ("neue zeile", "neuer zeile", "zeilenumbruch"):
        return Command("newline")
    if t in ("neuer absatz", "neue absatz", "absatz"):
        return Command("paragraph")
    if t in ("großschreiben", "gross schreiben", "groß schreiben",
             "großbuchstabe", "schreib groß", "schreibe groß", "schreib gross",
             "schreib das groß", "schreibe das groß", "mach groß",
             "mach das groß", "das groß"):
        return Command("capitalize", {"mode": "upper"})
    if t in ("kleinschreiben", "klein schreiben", "kleinbuchstabe",
             "schreib klein", "schreibe klein", "schreib das klein",
             "mach klein", "das klein"):
        return Command("capitalize", {"mode": "lower"})
    if t in PUNCT_WORDS:
        return Command("punct", {"char": PUNCT_WORDS[t]})
    if t in ("klammer auf", "runde klammer auf"):
        return Command("punct", {"char": "(", "glue": "left"})
    if t in ("klammer zu", "runde klammer zu"):
        return Command("punct", {"char": ")"})
    if t in ("anführungszeichen auf", "anfuehrungszeichen auf",
             "gänsefüßchen auf"):
        return Command("punct", {"char": "„", "glue": "left"})
    if t in ("anführungszeichen zu", "anfuehrungszeichen zu",
             "gänsefüßchen zu"):
        return Command("punct", {"char": "“"})
    return None


_MATCHERS = (_m_window, _m_mode, _m_correct, _m_pick, _m_delete, _m_select,
             _m_navigate, _m_format)


def parse(transcript: str) -> Command | None:
    """Return the :class:`Command` for a full utterance, or ``None`` for plain
    dictation text."""
    t = normalise(transcript)
    if not t:
        return None
    for matcher in _MATCHERS:
        cmd = matcher(t)
        if cmd is None:
            continue
        # For commands that insert verbatim text, keep the ORIGINAL casing
        # (matching was done on the lower-cased string only for detection).
        orig = _orig(transcript)
        if cmd.kind == "literal":
            m = re.fullmatch(r"(?:wörtlich|woertlich)\s+(.+)", orig, re.IGNORECASE)
            if m:
                cmd.data["text"] = _clean_target(m.group(1))
        elif cmd.kind == "replace":
            m = re.fullmatch(r"ersetze\s+(.+?)\s+durch\s+(.+)", orig, re.IGNORECASE)
            if m:
                cmd.data["from"] = _clean_target(m.group(1))
                cmd.data["to"] = _clean_target(m.group(2))
        return cmd
    return None

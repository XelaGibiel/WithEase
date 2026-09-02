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

# Inline punctuation: unambiguous spoken symbols that should become the symbol
# even in the middle of a dictated sentence ("Preis Doppelpunkt zehn" → "Preis:
# zehn").  Deliberately excludes "Punkt"/"Komma" (common words; Whisper already
# auto-punctuates sentences).  Grouped by spacing behaviour.
_INLINE_TIGHT = {"schrägstrich": "/", "schragstrich": "/", "bindestrich": "-"}
_INLINE_OPEN = {
    "runde klammer auf": "(", "eckige klammer auf": "[", "klammer auf": "(",
    "anführungszeichen auf": "„", "anfuehrungszeichen auf": "„",
    "anführungszeichen unten": "„", "anfuehrungszeichen unten": "„",
    "gänsefüßchen auf": "„", "gänsefüßchen unten": "„",
}
_INLINE_CLOSE = {
    "runde klammer zu": ")", "eckige klammer zu": "]", "klammer zu": ")",
    "anführungszeichen zu": "“", "anfuehrungszeichen zu": "“",
    "anführungszeichen oben": "“", "anfuehrungszeichen oben": "“",
    "gänsefüßchen zu": "“", "gänsefüßchen oben": "“", "doppelpunkt": ":",
    "semikolon": ";", "strichpunkt": ";", "gedankenstrich": "–",
    "ausrufezeichen": "!", "fragezeichen": "?",
}


def _inline_replace(text: str, mapping: dict, left: str, right: str) -> str:
    for phrase in sorted(mapping, key=len, reverse=True):
        body = r"\b" + r"\s+".join(re.escape(w) for w in phrase.split()) + r"\b"
        text = re.sub(left + body + right,
                      lambda _m, s=mapping[phrase]: s, text, flags=re.IGNORECASE)
    return text


def apply_inline_punctuation(text: str) -> str:
    """Turn spoken punctuation words inside dictated text into symbols.

    Whisper tends to treat a spoken punctuation word as its own sentence and
    wraps it in periods ("Wort. Fragezeichen." → we want "Wort?"), so the
    surrounding stray sentence punctuation is absorbed too."""
    if not text or not text.strip():
        return text
    # tight both sides (slash/hyphen): swallow surrounding spaces + stray dots
    text = _inline_replace(text, _INLINE_TIGHT, r"[\s.]*", r"[\s.]*")
    # closers: no space before; eat a stray period Whisper put before *and*
    # directly after (but keep the space before the next word)
    text = _inline_replace(text, _INLINE_CLOSE, r"[\s.,;:!?]*", r"\.?")
    # openers: no space after; eat a stray period Whisper put after
    text = _inline_replace(text, _INLINE_OPEN, r"", r"[\s.]*")
    return text

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
    # Checked BEFORE the plain "einfügen": hand the text over but keep the
    # window open, so a long text can be dictated paragraph by paragraph
    # without reopening and re-picking the target every time.
    if t in ("einfügen und weiter", "einfuegen und weiter",
             "übernehmen und weiter", "uebernehmen und weiter",
             "text einfügen und weiter", "text einfuegen und weiter",
             "einfügen und offen lassen", "einfuegen und offen lassen"):
        return Command("insert", {"keep_open": True})
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
    if t in ("nochmal", "noch mal", "noch einmal", "das nochmal",
             "nochmal aufnehmen", "neu aufnehmen", "nochmal sprechen",
             "satz wiederholen", "letzten satz wiederholen",
             "letzten satz neu"):
        return Command("redo_dictation")
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
             "anführungszeichen unten", "anfuehrungszeichen unten",
             "gänsefüßchen auf", "gänsefüßchen unten"):
        return Command("punct", {"char": "„", "glue": "left"})
    if t in ("anführungszeichen zu", "anfuehrungszeichen zu",
             "anführungszeichen oben", "anfuehrungszeichen oben",
             "gänsefüßchen zu", "gänsefüßchen oben"):
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


# ---------------------------------------------------------------------------
# Sentence navigation, numbers, date/time and text snippets
# ---------------------------------------------------------------------------

# Spoken number words → digits.  Only the ones people actually dictate as a
# figure ("Ziffer fünf" → "5"); anything else stays as Whisper wrote it.
NUMBER_WORDS = {
    "null": "0", "eins": "1", "ein": "1", "eine": "1", "zwei": "2", "zwo": "2",
    "drei": "3", "vier": "4", "fünf": "5", "fuenf": "5", "sechs": "6",
    "sieben": "7", "acht": "8", "neun": "9", "zehn": "10", "elf": "11",
    "zwölf": "12", "zwoelf": "12", "dreizehn": "13", "vierzehn": "14",
    "fünfzehn": "15", "fuenfzehn": "15", "sechzehn": "16", "siebzehn": "17",
    "achtzehn": "18", "neunzehn": "19", "zwanzig": "20", "dreißig": "30",
    "dreissig": "30", "vierzig": "40", "fünfzig": "50", "fuenfzig": "50",
    "hundert": "100", "tausend": "1000",
}


def _m_sentence_nav(t: str) -> Command | None:
    """Move sentence by sentence – the gap that made longer texts tedious."""
    if t in ("nächster satz", "naechster satz", "zum nächsten satz",
             "zum naechsten satz", "satz weiter"):
        return Command("next_sentence")
    if t in ("vorheriger satz", "voriger satz", "zum vorherigen satz",
             "letzter satz", "satz zurück", "satz zurueck"):
        return Command("prev_sentence")
    if t in ("lösche den letzten satz", "loesche den letzten satz",
             "lösche letzten satz", "loesche letzten satz"):
        return Command("delete_sentence", {"which": "last"})
    return None


def _m_number_date(t: str) -> Command | None:
    """Figures and today's date/time – Whisper writes numbers inconsistently,
    and a date is something you should not have to spell out."""
    m = re.fullmatch(r"(?:ziffer|zahl)\s+(.+)", t)
    if m:
        word = m.group(1).strip()
        if word in NUMBER_WORDS:
            return Command("literal", {"text": NUMBER_WORDS[word]})
        digits = "".join(NUMBER_WORDS.get(w, "") for w in word.split())
        if digits:
            return Command("literal", {"text": digits})
        return None
    if t in ("datum", "datum heute", "heutiges datum", "das datum",
             "datum einfügen", "datum einfuegen"):
        return Command("insert_date")
    if t in ("uhrzeit", "die uhrzeit", "uhrzeit einfügen",
             "uhrzeit einfuegen", "wie spät ist es"):
        return Command("insert_time")
    return None


def _m_help_history(t: str) -> Command | None:
    """Reach the reference and the history WITHOUT the mouse.

    Both were button-only – in a program whose whole point is avoiding precise
    pointing, the list of voice commands should itself be reachable by voice."""
    if t in ("hilfe", "befehle", "welche befehle gibt es",
             "welche befehle gibt es denn", "zeig die befehle",
             "zeige die befehle", "befehlsliste", "sprachbefehle"):
        return Command("show_help")
    m = re.fullmatch(r"(?:verlauf|aus dem verlauf|verlauf eintrag)\s+(\d+)", t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 20:
            return Command("history_pick", {"n": n})
    m = re.fullmatch(r"(?:verlauf|aus dem verlauf)\s+(.+)", t)
    if m and m.group(1) in _NUMBER_TO_INT:
        return Command("history_pick", {"n": _NUMBER_TO_INT[m.group(1)]})
    if t in ("verlauf", "verlauf zeigen", "zeig den verlauf",
             "zeige den verlauf"):
        return Command("history_show")
    return None


def _m_snippet(t: str) -> Command | None:
    """Insert a saved text block by name – the biggest saving for anyone who
    has to speak every single word of a phrase they use daily."""
    m = re.fullmatch(r"(?:füge|fuege|einfügen|einfuegen)\s+(.+?)\s+ein", t)
    if m:
        return Command("snippet", {"name": _clean_target(m.group(1))})
    m = re.fullmatch(r"(?:baustein|textbaustein|vorlage)\s+(.+)", t)
    if m:
        return Command("snippet", {"name": _clean_target(m.group(1))})
    return None


# Registered BEFORE _m_window so "füge Grußformel ein" is not swallowed by the
# bare "einfügen" window command, and before _m_format so "Ziffer fünf" wins
# over the punctuation matcher.
# Spoken ordinals for "Verlauf zwei".
_NUMBER_TO_INT = {w: int(d) for w, d in NUMBER_WORDS.items()
                  if d.isdigit() and 1 <= int(d) <= 20}

_MATCHERS = (_m_help_history, _m_snippet, _m_number_date,
             _m_sentence_nav) + _MATCHERS


# ---------------------------------------------------------------------------
# Self-description – the single source for the cheat sheet
# ---------------------------------------------------------------------------

# (group, [(example utterance, what it does)]).  Kept HERE, next to the
# matchers, so the reference cannot drift away from the grammar the way a
# separate hand-written HTML block did.
CHEAT_SHEET: list[tuple[str, list[tuple[str, str]]]] = [
    ("Diktieren", [
        ("einfach sprechen", "Text einfügen – groß/klein und Leerzeichen "
                             "richten sich nach dem, was davor steht"),
        ("neue Zeile", "Zeilenumbruch"),
        ("neuer Absatz", "Leerzeile und neuer Absatz"),
        ("Punkt · Komma · Fragezeichen", "Satzzeichen einfügen"),
        ("großschreiben · kleinschreiben", "nächstes Wort groß/klein"),
        ("wörtlich <Text>", "Text einfügen, auch wenn er wie ein Befehl klingt"),
        ("buchstabiere Anton Berta …", "buchstabiert einfügen"),
    ]),
    ("Zahlen, Datum, Bausteine", [
        ("Ziffer fünf", "als Zahl einfügen: 5"),
        ("Datum heute", "heutiges Datum einfügen"),
        ("Uhrzeit", "aktuelle Uhrzeit einfügen"),
        ("füge <Name> ein", "gespeicherten Textbaustein einfügen"),
        ("Baustein <Name>", "dasselbe, kürzer gesprochen"),
    ]),
    ("Bewegen", [
        ("Cursor vor <Wort>", "Cursor vor dieses Wort setzen"),
        ("hinter <Wort>", "Cursor dahinter setzen"),
        ("nächster Satz · Satz zurück", "Satz für Satz bewegen"),
        ("an den Anfang · ans Ende", "an den Textanfang/-schluss"),
    ]),
    ("Auswählen", [
        ("markiere <Wort>", "dieses Wort markieren"),
        ("markiere von A bis B", "alles dazwischen markieren"),
        ("markiere diesen Satz", "den Satz am Cursor markieren"),
        ("markiere diesen Absatz", "den Absatz am Cursor markieren"),
        ("markiere alles", "gesamten Text markieren"),
        ("nimm 2", "bei mehreren Treffern den richtigen wählen (1 bis 9)"),
    ]),
    ("Löschen", [
        ("lösche <Wort>", "dieses Wort löschen"),
        ("lösche das", "Markierung oder zuletzt Eingefügtes löschen"),
        ("lösche diesen Satz", "den Satz am Cursor löschen"),
        ("lösche den letzten Satz", "den letzten Satz im Text löschen"),
        ("alles löschen", "Fenster leeren"),
    ]),
    ("Korrigieren", [
        ("korrigiere das", "Korrekturfenster zum zuletzt Eingefügten"),
        ("korrigiere <Wort>", "Korrekturfenster zu diesem Wort"),
        ("ersetze A durch B", "A durch B ersetzen"),
        ("rückgängig · wiederholen", "letzte Änderung zurück/erneut"),
    ]),
    ("Hilfe und Verlauf", [
        ("Hilfe", "diese Befehlsliste öffnen"),
        ("Verlauf", "die letzten Diktate einblenden"),
        ("Verlauf 2", "das zweitletzte Diktat zurückholen (1 bis 9)"),
    ]),
    ("Fenster", [
        ("einfügen", "Text in die Ziel-App einfügen und schließen"),
        ("einfügen und weiter", "einfügen, Fenster bleibt für den nächsten "
                                "Absatz offen"),
        ("kopieren", "Text in die Zwischenablage"),
        ("Ziel wählen", "andere Ziel-App bestimmen"),
        ("Fenster schließen", "Diktierfenster schließen"),
    ]),
]


def cheat_sheet_rows() -> list[tuple[str, str, str]]:
    """Flat ``(group, utterance, meaning)`` rows – what the reference renders
    and what its search filters."""
    return [(group, said, means)
            for group, items in CHEAT_SHEET for said, means in items]

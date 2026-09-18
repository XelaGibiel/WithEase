"""Live dictation (test variant): text appears while you speak.

The normal dictation records first and recognises afterwards - clean, but
nothing is visible until the key is released.  This variant recognises the
sentence that is being spoken over and over again, every half second, and
shows the result straight away:

  * words the recogniser produced **twice in a row** are taken as settled and
    turn black ("local agreement" - the method from *Turning Whisper into a
    Real-Time Transcription System*, Macháček et al. 2023);
  * the rest stays grey and may still change with the next pass;
  * a real pause ends the sentence: it is recognised one last time as a whole
    and finished with the same rules as a normal dictation (dictionary,
    learned corrections, commas, optional AI punctuation, voice commands).

Settled words keep their place but take the spelling of the newest pass, so a
comma or a capital letter that only becomes clear later still arrives while
speaking - the word itself never jumps.

Nothing here knows about Whisper or Parakeet: a recogniser is any function
``transcribe(pcm, final) -> str`` for 16 kHz mono int16.  That keeps the two
engines comparable, and keeps this file testable without a model.
"""
from __future__ import annotations

import collections
import queue
import re
import threading
import time
from typing import Callable

RATE = 16000
_BYTES_PER_S = RATE * 2              # int16 mono

_WORD_CORE = re.compile(r"[^\w]+", re.UNICODE)


def _norm(word: str) -> str:
    """A word without its punctuation and case - what "the same word" means."""
    return _WORD_CORE.sub("", word).casefold()


class Agreement:
    """Settles words once two passes in a row agree on them."""

    def __init__(self) -> None:
        self.settled: list[str] = []
        self._previous: list[str] = []

    def reset(self) -> None:
        self.settled = []
        self._previous = []

    def update(self, words: list[str]) -> tuple[list[str], list[str]]:
        """Feed one pass.  Returns ``(settled, tail)`` for display."""
        prev = [_norm(w) for w in self._previous]
        now = [_norm(w) for w in words]
        agree = 0
        while (agree < len(prev) and agree < len(now)
               and prev[agree] == now[agree] and now[agree]):
            agree += 1
        if agree > len(self.settled):
            self.settled = list(words[:agree])
        self._previous = list(words)

        n = len(self.settled)
        # Settled words take the newest spelling where the word is unchanged
        # (a comma or capital that became clear later), never a new word.
        for i in range(min(n, len(words))):
            if _norm(words[i]) == _norm(self.settled[i]):
                self.settled[i] = words[i]
        tail = list(words[n:]) if len(words) > n else []
        return list(self.settled), tail


class Levels:
    """Tells speech from room noise, following the room.

    A fixed threshold is either too deaf for a far-field microphone or too
    eager next to a fan.  The floor follows the quietest recent audio; speech
    is clearly above it."""

    def __init__(self, gate: float = 0.0) -> None:
        self._floor = 200.0
        self._gate = gate              # a user-set minimum, 0 = automatic

    def is_speech(self, rms: float) -> bool:
        if rms < self._floor:
            self._floor = max(30.0, 0.7 * self._floor + 0.3 * rms)
        else:
            self._floor = min(3000.0, self._floor * 1.002)
        threshold = max(self._gate, self._floor * 2.5, 120.0)
        return rms >= threshold


class EnergyGate:
    """Loudness only - the fallback when the speech model is not there."""

    def __init__(self, gate: float = 0.0) -> None:
        self._levels = Levels(gate)

    def is_speech(self, chunk: bytes, in_speech: bool = False) -> bool:
        return self._levels.is_speech(rms16(chunk))


class SileroGate:
    """Tells speech from everything else with the Silero speech model.

    Loudness cannot tell a voice from a door, a keyboard, a cough or music -
    they are all "loud".  This small model (well under a millisecond per
    call) was trained for exactly that question, and faster-whisper already
    ships it.

    Hysteresis: a sentence only STARTS on clear speech, but once it runs a
    softer, trailing voice keeps it going - so quiet word endings are not
    mistaken for the pause that ends the sentence.
    """

    WINDOW = 512                     # samples; what the model is built for
    HISTORY = 16                     # windows of context per call (~0.5 s)

    def __init__(self, model, start: float = 0.5, keep: float = 0.35) -> None:
        self._model = model
        self.start = start
        self.keep = keep
        self._pending = b""
        self._history = None
        self.last_probability = 0.0

    def _threshold(self, in_speech: bool) -> float:
        return self.keep if in_speech else self.start

    def is_speech(self, chunk: bytes, in_speech: bool = False) -> bool:
        import numpy as np
        data = self._pending + chunk
        usable = len(data) // (self.WINDOW * 2) * self.WINDOW * 2
        self._pending = data[usable:]
        if not usable:
            return self.last_probability >= self._threshold(in_speech)
        fresh = np.frombuffer(data[:usable], dtype=np.int16).astype(
            np.float32) / 32768.0
        history = (fresh if self._history is None
                   else np.concatenate([self._history, fresh]))
        self._history = history[-self.HISTORY * self.WINDOW:]
        try:
            probs = np.asarray(self._model(self._history)).reshape(-1)
        except Exception:
            return False
        new_windows = max(1, len(fresh) // self.WINDOW)
        self.last_probability = float(probs[-new_windows:].max())
        return self.last_probability >= self._threshold(in_speech)


def make_gate(sensitivity: str = "normal"):
    """The best available speech gate.  ``sensitivity``: "normal", or "strict"
    for a noisy room (clearer speech needed before a sentence starts)."""
    start, keep = (0.7, 0.45) if sensitivity == "strict" else (0.5, 0.35)
    try:
        from faster_whisper.vad import get_vad_model
        return SileroGate(get_vad_model(), start=start, keep=keep)
    except Exception:
        return EnergyGate()


# Sounds a recogniser writes down when there were no words: a car passing
# became "Mm-hmm".  Only as whole words - "Hmmel" or "Umzug" stay.
_FILLERS = re.compile(
    r"(?<![\w-])(?:m+-?h+m+|m+h+m+|h+m+|u+h+-?h+u+h+|u+h+|u+m+|ä+h+m*|ö+h*m+)"
    r"(?![\w-])[.,!?…]*", re.IGNORECASE)

_EN = frozenset("""the a an and or but is are was were be been it this that you
i we they he she my your our to of in on for with not do does did have has
had what how why where when can could would should will just so if there
here all no yes okay hello hi thanks thank please""".split())
_DE = frozenset("""der die das und oder aber ist sind war waren ein eine einen
es ich wir sie er du mein dein nicht mit auf für von zu im in den dem des
auch noch schon wie was warum wo wann kann könnte würde soll wird nur so
wenn hier alle nein ja bitte danke hallo""".split())


def clean_fillers(text: str) -> str:
    """Remove "Mm-hmm", "Hmm", "Uh", "Äh" … and tidy what is left."""
    cleaned = _FILLERS.sub(" ", text or "")
    cleaned = re.sub(r"\s+([.,!?…])", r"\1", cleaned)
    cleaned = re.sub(r"^[\s.,!?…]+", "", cleaned)
    cleaned = " ".join(cleaned.split())
    original = (text or "").lstrip()
    if (cleaned and original[:1].isupper() and cleaned[:1].islower()
            and not original.lower().startswith(cleaned[:3].lower())):
        # the filler opened the sentence; the sentence still starts capital
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def looks_foreign(text: str, language: str = "de") -> bool:
    """True for a sentence that is clearly English while dictating German.

    Parakeet chooses the language by itself, so a voice from the TV or the
    next room comes out as an English sentence.  Needs several English
    function words and more of them than German ones - a single "okay" or
    an English product name never counts."""
    if language != "de":
        return False
    words = re.findall(r"[a-zA-ZäöüÄÖÜß']+", (text or "").lower())
    if len(words) < 3:
        return False
    english = sum(1 for w in words if w in _EN)
    german = sum(1 for w in words if w in _DE)
    return english >= 2 and english > 2 * german


# -- sentence marks ----------------------------------------------------------

# Words that do not open a German sentence of their own: after a thinking
# pause they continue the sentence before, so a full stop in front of them
# was the pause, not the end.  Deliberately NOT here: "wenn", "ob",
# "obwohl", "aber", "denn" - they open sentences all the time.
CONTINUATIONS = frozenset(
    "und oder sondern sowie bzw beziehungsweise dass sodass weil damit "
    "wobei weshalb wodurch womit".split())
# ... and the ones that take no comma in front of them.
NO_COMMA = frozenset("und oder sowie bzw beziehungsweise".split())

_ABBREVIATIONS = frozenset(
    "usw bzw ca etc evtl ggf inkl vgl nr dr hr fr str tel abs z.b d.h u.a "
    "o.ä s.o u.u".split())


# These DO open sentences - "Wenn es regnet, bleibe ich zu Hause." - but
# such a sentence then has a comma before its main clause.  Without one,
# "Wenn es wieder vorkommt." is the tail of the sentence before it.
TRAILING_CLAUSES = frozenset(
    "wenn ob obwohl falls sobald solange bevor nachdem sofern".split())


def continues(word: str, rest_of_sentence: str) -> bool:
    """Whether a sentence starting with ``word`` continues the one before."""
    lower = word.lower()
    if lower in CONTINUATIONS:
        return True
    if lower in TRAILING_CLAUSES:
        clause = re.split(r"[.!?]", rest_of_sentence, maxsplit=1)[0]
        return "," not in clause
    return False


def merge_continuations(text: str) -> str:
    """"… besser Pausen. Und dann" -> "… besser Pausen und dann"."""
    text = text or ""

    def join(match: re.Match) -> str:
        word = match.group(1)
        if not continues(word, text[match.end():]):
            return match.group(0)
        lower = word.lower()
        return (" " if lower in NO_COMMA else ", ") + lower

    return re.sub(r"\.\s+([A-ZÄÖÜ][a-zäöüß]+)\b", join, text)


# -- short words ---------------------------------------------------------------

# Parakeet picks the language by itself, and a single short German word is
# too little to go on: "ja" came out as "Yeah", "hat" as "Had".  Only when
# the whole utterance is that one word.
_SHORT_ENGLISH = {"yeah": "Ja", "yes": "Ja", "yep": "Ja", "nine": "Nein",
                  "nay": "Nein", "had": "Hat", "is": "Ist", "east": "Ist",
                  "hut": "Hat", "dust": "Du", "ish": "Ich"}


def fix_short_english(text: str, language: str = "de") -> str:
    if language != "de":
        return text
    words = re.findall(r"[A-Za-z]+", text or "")
    if len(words) != 1:
        return text
    german = _SHORT_ENGLISH.get(words[0].lower())
    return text.replace(words[0], german, 1) if german else text


# -- numbers and signs ------------------------------------------------------------

_UNITS = {"null": 0, "eins": 1, "ein": 1, "eine": 1, "zwei": 2, "zwo": 2,
          "drei": 3, "vier": 4, "fünf": 5, "fuenf": 5, "sechs": 6,
          "sieben": 7, "acht": 8, "neun": 9}
_TEENS = {"zehn": 10, "elf": 11, "zwölf": 12, "zwoelf": 12, "dreizehn": 13,
          "vierzehn": 14, "fünfzehn": 15, "fuenfzehn": 15, "sechzehn": 16,
          "siebzehn": 17, "achtzehn": 18, "neunzehn": 19}
_TENS = {"zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40,
         "fünfzig": 50, "fuenfzig": 50, "sechzig": 60, "siebzig": 70,
         "achtzig": 80, "neunzig": 90}


def _below_100(word: str) -> int | None:
    if word in _TEENS:
        return _TEENS[word]
    if word in _UNITS and word not in ("ein", "eine"):
        return _UNITS[word]
    if word in _TENS:
        return _TENS[word]
    for tens, value in _TENS.items():
        if word.endswith(tens):
            head = word[:-len(tens)]
            # "siebenundvierzig" - and "siebenvierzig", as it is misheard
            head = head[:-3] if head.endswith("und") else head
            if head in _UNITS and _UNITS[head] > 0:
                return value + _UNITS[head]
    return None


def _below_1000(word: str) -> int | None:
    if "hundert" in word:
        head, _, tail = word.partition("hundert")
        count = 1 if head in ("", "ein", "eins") else _below_100(head)
        rest = _below_100(tail) if tail else 0
        if count is None or rest is None or not head:
            return None               # a bare "hundert" stays a word
        return count * 100 + rest
    return _below_100(word)


def number_value(word: str) -> int | None:
    """The value of one German number word, or None."""
    word = (word or "").lower()
    if "tausend" in word:
        head, _, tail = word.partition("tausend")
        count = 1 if head in ("ein", "eins") else _below_1000(head)
        rest = _below_1000(tail) if tail else 0
        if not head or count is None or rest is None:
            return None
        return count * 1000 + rest
    return _below_1000(word)


# After these a small number is a quantity to read, so it becomes a digit too.
_UNIT_WORDS = frozenset(
    "prozent euro cent uhr grad kilo kilogramm gramm meter kilometer "
    "zentimeter millimeter liter stück minuten sekunden stunden "
    "paragraph paragraf paragraphen paragrafen".split())


# -- your own words ------------------------------------------------------------

def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text or "").casefold()


# German endings: a dictionary word plus one of these is the same word
# inflected ("Rechnung" -> "Rechnungen"), not a misspelling of it.
_ENDINGS = ("e", "en", "n", "s", "es", "er", "ern", "em")


def _special_spelling(word: str) -> bool:
    """A name written its own way: several words, a capital inside
    ("MediaMarkt"), all capitals ("ADAC") or digits."""
    letters = re.sub(r"[^\w]", "", word)
    return (" " in word.strip() or "-" in word
            or any(c.isupper() for c in letters[1:])
            or any(c.isdigit() for c in letters))


def _inflected(a: str, b: str) -> bool:
    short, long = sorted((a, b), key=len)
    return long.startswith(short) and long[len(short):] in _ENDINGS


def cologne_code(word: str) -> str:
    """Kölner Phonetik: German words that sound alike get the same code
    ("Parakit" and "Parakeet", "Meier" and "Mayer")."""
    w = (_key(word).replace("ä", "a").replace("ö", "o").replace("ü", "u")
         .replace("ß", "s"))
    codes = []
    for i, ch in enumerate(w):
        prev = w[i - 1] if i else ""
        nxt = w[i + 1] if i + 1 < len(w) else ""
        if ch in "aeijouy":
            code = "0"
        elif ch == "h":
            code = ""
        elif ch == "b" or (ch == "p" and nxt != "h"):
            code = "1"
        elif ch in "dt":
            code = "8" if nxt in ("c", "s", "z") else "2"
        elif ch in "fvw" or (ch == "p" and nxt == "h"):
            code = "3"
        elif ch in "gkq":
            code = "4"
        elif ch == "c":
            if i == 0:
                code = "4" if nxt in "ahkloqrux" else "8"
            else:
                code = ("8" if prev in "sz" or nxt not in "ahkoqux" else "4")
        elif ch == "x":
            code = "8" if prev in "ckq" else "48"
        elif ch == "l":
            code = "5"
        elif ch in "mn":
            code = "6"
        elif ch == "r":
            code = "7"
        elif ch in "sz":
            code = "8"
        else:
            code = ""
        codes.append(code)
    out = ""
    for code in "".join(codes):
        if not out or out[-1] != code:
            out += code
    return out[:1] + out[1:].replace("0", "") if out else ""


def match_dictionary(text: str, words: list[str],
                     similarity: float = 0.86) -> str:
    """Put your dictionary words back where the recogniser wrote them
    differently.

    Whisper is TOLD these words before it listens; Parakeet cannot be, so
    they are matched afterwards instead:

    * the same letters, spaced or cased differently ("Media Markt" ->
      "MediaMarkt", "withease" -> "WithEase") - always;
    * a close misspelling ("Mediamark" -> "MediaMarkt") - only for words of
      six letters or more, starting with the same letter, and never when the
      difference is just an ending, so "Rechnungen" stays "Rechnungen" even
      if "Rechnung" is in the dictionary.
    """
    import difflib
    entries = []
    for word in words or []:
        key = _key(word)
        if len(key) >= 4:
            entries.append((word, key, max(1, len(word.split()))))
    if not entries or not text:
        return text
    spans = [(m.start(), m.end()) for m in re.finditer(r"[\w'’-]+", text)]
    replacements: list[tuple[int, int, str]] = []
    taken: set[int] = set()
    for size in (3, 2, 1):
        for i in range(len(spans) - size + 1):
            if any(j in taken for j in range(i, i + size)):
                continue
            start, end = spans[i][0], spans[i + size - 1][1]
            surface = text[start:end]
            key = _key(surface)
            tokens = surface.split()
            capitalised = all(t[:1].isupper() or t[:1].isdigit()
                              for t in re.findall(r"[\w'’-]+", surface))
            best, best_score = None, 0.0
            for word, wkey, parts in entries:
                if size > parts + 1:
                    continue
                special = _special_spelling(word)
                # Joining several words into one ("Nach Namen" ->
                # "Nachnamen") only for names that are written that way.
                if size > parts and not special:
                    continue
                if key == wkey:
                    # Only the case differs: "schreiben"/"Schreiben" is
                    # grammar, "withease"/"WithEase" is a name.
                    if size == 1 and len(tokens) == 1 and not special:
                        continue
                    score = 1.0
                elif (len(wkey) >= 6 and key[:1] == wkey[:1] and capitalised
                      and abs(len(key) - len(wkey)) <= max(2, len(wkey) // 4)
                      and not _inflected(key, wkey)):
                    # A misheard name comes out capitalised; a lower-case
                    # word ("beginnt", "wie das") is an ordinary word.
                    score = difflib.SequenceMatcher(None, key, wkey).ratio()
                    if score < similarity:
                        # written differently, but does it SOUND the same?
                        if score < 0.7 or cologne_code(key) != cologne_code(wkey):
                            continue
                        score = similarity
                else:
                    continue
                if score > best_score:
                    best, best_score = word, score
            if best is not None and surface != best:
                replacements.append((start, end, best))
                taken.update(range(i, i + size))
            elif best is not None:
                taken.update(range(i, i + size))
    for start, end, word in sorted(replacements, reverse=True):
        text = text[:start] + word + text[end:]
    return text


def spoken_numbers(text: str) -> str:
    """Number words to digits, "plus" and "Prozent" to signs.

    German style: from 13 on numbers are written as digits; 0-12 stay words
    ("zwei Wochen") unless a sign, a unit or another digit stands next to
    them ("plus sieben", "sieben Prozent")."""
    if not text:
        return text
    # "sieben und vierzig" written apart (Canary does) is one number
    text = re.sub(
        r"\b(ein|zwei|drei|vier|fünf|fuenf|sechs|sieben|acht|neun)\s+und\s+"
        r"(zwanzig|dreißig|dreissig|vierzig|fünfzig|fuenfzig|sechzig|siebzig|"
        r"achtzig|neunzig)\b",
        lambda m: m.group(1) + "und" + m.group(2), text, flags=re.IGNORECASE)
    tokens = re.findall(r"\S+", text)

    def core(token: str) -> tuple[str, str, str]:
        m = re.match(r"^([„(\[]*)(.*?)([.,;:!?)\]“]*)$", token)
        return m.group(1), m.group(2), m.group(3)

    # 1) join "zweitausend sechsundzwanzig" said as two words
    values: list[int | None] = []
    for token in tokens:
        _lead, word, _trail = core(token)
        values.append(number_value(word))
    out: list[str] = []
    i = 0
    while i < len(tokens):
        lead, word, trail = core(tokens[i])
        value = values[i]
        if (value is not None and not trail and i + 1 < len(tokens)
                and values[i + 1] is not None
                and word.lower().endswith(("tausend", "hundert"))
                and values[i + 1] < (1000 if word.lower().endswith("tausend")
                                     else 100)):
            _l2, _w2, trail = core(tokens[i + 1])
            value += values[i + 1]
            i += 1
        out.append((lead, word, trail, value))
        i += 1

    def is_sign(word: str) -> bool:
        return word.lower() in ("plus", "minus")

    def next_to_quantity(k: int) -> bool:
        for j in (k - 1, k + 1):
            if 0 <= j < len(out):
                _l, w, _t, v = out[j]
                if (is_sign(w) or w.lower() in _UNIT_WORDS
                        or (w and w[0].isdigit())
                        or (v is not None and v >= 13)):
                    return True
        return False

    # 2) numbers to digits
    words = []
    for k, (lead, word, trail, value) in enumerate(out):
        if value is not None and (value >= 13 or next_to_quantity(k)):
            word = str(value)
        words.append([lead, word, trail])

    # 3) signs: only next to numbers or another sign ("C plus plus")
    def numeric(k: int) -> bool:
        return 0 <= k < len(words) and bool(
            re.fullmatch(r"[+-]?\d+([.,]\d+)?", words[k][1]))

    def signish(k: int) -> bool:
        return 0 <= k < len(words) and words[k][1].lower() in (
            "plus", "minus", "+", "-", "++")

    def drop(k: int, into: int) -> None:
        """Remove word ``k``; its trailing punctuation moves to ``into``."""
        words[into][2] += words[k][2]
        words[k] = ["", "", ""]

    for k, entry in enumerate(words):
        low = entry[1].lower()
        if not low:
            continue
        if low == "plus" and (numeric(k + 1) or numeric(k - 1)
                              or signish(k + 1) or signish(k - 1)):
            entry[1] = "+"
        elif low == "minus" and numeric(k + 1):
            if numeric(k - 1) and not words[k - 1][2]:
                entry[1] = "-"                        # 3 - 2
            else:                                     # minus 5 Grad -> -5
                words[k + 1][0] = entry[0] + words[k + 1][0]
                words[k + 1][1] = "-" + words[k + 1][1]
                words[k] = ["", "", ""]
        elif low == "prozent" and numeric(k - 1):
            entry[1] = "%"
        elif low == "euro" and numeric(k - 1):
            entry[1] = "€"
        elif low == "grad" and numeric(k - 1):
            if (k + 1 < len(words) and not entry[2]
                    and words[k + 1][1].lower() == "celsius"):
                entry[1] = "°C"                       # 5 °C
                drop(k + 1, k)
            else:
                words[k - 1][1] += "°"                # a 90° angle
                drop(k, k - 1)
        elif low in ("paragraph", "paragraf") and numeric(k + 1):
            entry[1] = "§"
        elif low in ("paragraphen", "paragrafen") and numeric(k + 1):
            entry[1] = "§§"
    words = [w for w in words if any(w)]

    # 4) glue: "+ + 47" -> "++47", "C + +" -> "C++"; "5 + 3" keeps spaces
    text_out = ""
    for k, (lead, word, trail) in enumerate(words):
        piece = lead + word + trail
        prev = words[k - 1][1] if k else ""
        glue = (k and (
            (word == "+" and prev in ("+",) and not words[k - 1][2])
            or (prev == "+" and not words[k - 1][2] and word[:1].isdigit()
                and not (k >= 2 and numeric(k - 2)))
            or (word == "+" and k + 1 < len(words) and words[k + 1][1] == "+"
                and not numeric(k - 1) and prev and not words[k - 1][2]
                and len(prev) == 1)))
        text_out += (piece if glue or not text_out else " " + piece)
    # "fünf Euro fünfzig" -> "5,50 €"
    return re.sub(r"\b(\d+) € (\d{1,2})\b(?![,.]\d)",
                  lambda m: f"{m.group(1)},{int(m.group(2)):02d} €", text_out)



def strip_sentence_marks(text: str) -> str:
    """Remove the recogniser's own . ? ! - for "Satzzeichen nur gesprochen".

    Numbers ("3.5"), dates and abbreviations ("z.B.", "usw.") keep theirs."""
    def drop(match: re.Match) -> str:
        word = match.group(1)
        if word.lower().rstrip(".") in _ABBREVIATIONS or len(word) == 1:
            return match.group(0)
        return word

    text = re.sub(r"([\wäöüÄÖÜß.]*[^\W\d_])[.!?]+(?=\s|$)", drop, text or "")
    from postprocess import fix_casing
    return fix_casing(text)


# The spoken words for sentence marks.  "Punkt" and "Komma" are ordinary
# nouns too ("der Punkt ist", "ein Komma fehlt"), so after an article or a
# preposition they stay words.
_NOT_AFTER = (r"(?<!\bder )(?<!\bden )(?<!\bdem )(?<!\bdes )(?<!\bein )"
              r"(?<!\beinen )(?<!\beinem )(?<!\bkein )(?<!\bkeinen )"
              r"(?<!\bam )(?<!\bzum )(?<!\bbeim )(?<!\bim )(?<!\bvom )"
              r"(?<!\bdiesen )(?<!\bdiesem )(?<!\bjeden )")
_SPOKEN_MARKS = [
    (re.compile(r"[\s,]*\bneuer\s+satz\b[\s.]*", re.IGNORECASE), ". "),
    (re.compile(r"[\s,.]*" + _NOT_AFTER + r"\bpunkt\b[\s.]*", re.IGNORECASE),
     ". "),
    (re.compile(r"[\s,]*\bfragezeichen\b[\s.]*", re.IGNORECASE), "? "),
    (re.compile(r"[\s,]*\bausrufezeichen\b[\s.]*", re.IGNORECASE), "! "),
    (re.compile(r"[\s,]*" + _NOT_AFTER + r"\bkomma\b[\s,.]*", re.IGNORECASE),
     ", "),
]


def apply_spoken_marks(text: str) -> str:
    """"hinbekommt Punkt neuer Gedanke" -> "hinbekommt. Neuer Gedanke"."""
    words = re.findall(r"\w+", (text or "").lower())
    if words and all(w in ("punkt", "komma", "fragezeichen",
                           "ausrufezeichen", "neuer", "satz") for w in words):
        return text        # only the mark: the voice command inserts it
    for pattern, mark in _SPOKEN_MARKS:
        text = pattern.sub(mark, text or "")
    text = " ".join(text.split())
    # a capital after a spoken full stop; the very first letter is left to
    # the joining, which knows what stands before it
    return re.sub(r"([.!?]\s+)([a-zäöü])",
                  lambda m: m.group(1) + m.group(2).upper(), text)


# -- context for single words ----------------------------------------------------

# Short words a recogniser really writes on their own; any other token of one
# or two letters left over at the cut is a crumb of the context sentence.
_SHORT_WORDS = frozenset(
    "ja so da du er es ob um zu in an im am wo ab oh ok ich wir sie der die "
    "das den dem ein und oder aber nur mit von bei auf aus".split())


def strip_context(full: str, context: str) -> str | None:
    """What was said AFTER the context sentence, or None when the two cannot
    be lined up safely.

    ``full`` is the recognition of context + the new utterance, ``context``
    the recognition of the context alone.  They are lined up word by word
    (the context may come out slightly differently the second time), and
    everything after the last shared word is the new utterance."""
    import difflib
    words = (full or "").split()
    ctx = [_norm(w) for w in (context or "").split() if _norm(w)]
    if not words or not ctx:
        return None
    normed = [_norm(w) for w in words]
    matcher = difflib.SequenceMatcher(None, ctx, normed, autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size]
    if not blocks or sum(b.size for b in blocks) < max(1, len(ctx) // 2):
        return None
    rest = words[blocks[-1].b + blocks[-1].size:]
    # the context's last words came out a little different the second time
    # ("haben?" / "habens.") - a close look-alike still belongs to it
    for tail_word in ctx[blocks[-1].a + blocks[-1].size:]:
        if rest and difflib.SequenceMatcher(
                None, tail_word, _norm(rest[0])).ratio() >= 0.6:
            rest = rest[1:]
    while rest and len(_norm(rest[0])) <= 2 and _norm(rest[0]) not in _SHORT_WORDS:
        rest = rest[1:]                 # "S." - a crumb of the last word
    text = " ".join(rest).lstrip(" .,;:!?…")
    return text or None


def rms16(chunk: bytes) -> float:
    try:
        import audioop
        return float(audioop.rms(chunk, 2))
    except Exception:
        try:
            import numpy as np
            a = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
            return float(np.sqrt(np.mean(a * a))) if len(a) else 0.0
        except Exception:
            return 0.0


class StreamSession:
    """Turns a stream of microphone chunks into live text.

    ``on_update(settled, tail)`` while a sentence is spoken,
    ``on_final(text)`` when it ended.  Both are called from the worker thread.
    """

    def __init__(self, transcribe: Callable[[bytes, bool], str],
                 on_update: Callable[[str, str], None],
                 on_final: Callable[[str], None], *,
                 step_s: float = 0.5, pause_s: float = 0.9,
                 keep_silence_s: float = 0.3,
                 preroll_s: float = 0.3, max_sentence_s: float = 25.0,
                 min_speech_s: float = 0.15, gate: float = 0.0,
                 first_pass_s: float = 0.0, detector=None,
                 background_ratio: float = 0.0,
                 voice_level: float | None = None,
                 short_s: float = 0.0, context_s: float = 5.0,
                 on_error: Callable[[Exception], None] | None = None,
                 on_silence: Callable[[float | None], None] | None = None,
                 ) -> None:
        self._transcribe = transcribe
        # ``on_silence(seconds left)`` while you are quiet in the middle of a
        # sentence: how long until the pause ends it.  None when that is
        # over (you speak again, or the sentence is finished).
        self._on_silence = on_silence
        self._silence_shown: float | None = None
        self._on_update = on_update
        self._on_final = on_final
        self._on_error = on_error
        self.step_s = step_s
        # Only a pause this long ends the sentence; shorter ones are for
        # thinking and leave it open.
        self.pause_s = pause_s
        # Silence beyond this is left out of what the recogniser hears: to
        # it the sentence sounds spoken in one go, so a thinking pause gives
        # it no reason to write a full stop.
        self.keep_silence_s = keep_silence_s
        self.preroll_s = preroll_s
        self.max_sentence_s = max_sentence_s
        self.min_speech_s = min_speech_s
        # Parakeet picks the language by itself and guesses English on the
        # first half second ("Constant." for "Kannst"); wait for more audio.
        self.first_pass_s = first_pass_s
        self._detector = detector or EnergyGate(gate)
        # Your own voice, learned from the sentences that became text.  A
        # sentence much quieter than that - a voice from the next room, the
        # TV - is someone else: ``background_ratio`` of your level is the
        # line (0 switches it off).
        self.background_ratio = background_ratio
        self.voice_level = voice_level
        self._levels_now: list[float] = []
        self.last_finish: dict = {}
        # A single word gives a recogniser nothing to go by - Parakeet then
        # guesses the language.  Heard after the previous sentence it is read
        # as German, like everything around it.  ``short_s``: utterances with
        # less speech than this get the previous sentence as context (0 = off).
        self.short_s = short_s
        self.context_s = context_s
        self._last_audio = b""
        self._agreement = Agreement()
        self._preroll: collections.deque[bytes] = collections.deque()
        self._preroll_bytes = 0
        self._sentence = bytearray()
        self._speech_bytes = 0
        self._silence_bytes = 0
        self._since_pass = 0
        self.queue: queue.Queue[bytes | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self.passes = 0                        # for tests and the log
        self.pass_ms: list[float] = []

    # -- feeding --------------------------------------------------------

    def put(self, pcm16: bytes) -> None:
        """Queue 16 kHz mono int16 audio (safe from the audio callback)."""
        self.queue.put(bytes(pcm16))

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="live-dictation")
        self._thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        """Finish the sentence that is still open, then end."""
        self.queue.put(None)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None

    # -- the loop ---------------------------------------------------------

    def _run(self) -> None:
        ending = False
        while not ending:
            try:
                chunk = self.queue.get(timeout=0.05)
            except queue.Empty:
                continue
            # Take everything that has piled up while the last pass ran: a
            # slow recogniser then simply works in larger steps instead of
            # falling further and further behind.
            chunks = [chunk]
            while True:
                try:
                    chunks.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            for c in chunks:
                if c is None:
                    ending = True
                    break
                self._take(c)
                # A pause inside what piled up still ends the sentence there,
                # or two sentences would reach the recogniser as one.
                if (self._sentence and self._silence_bytes
                        >= self.pause_s * _BYTES_PER_S):
                    self._finish("pause")
            self._decide(ending)
            self._report_silence()
        self._silence_shown = None
        if self._on_silence is not None:
            self._on_silence(None)

    # Quiet shorter than this is between two words, not a pause yet - a
    # countdown there would only flicker.
    _SILENCE_SHOWN_AFTER_S = 0.3

    def _report_silence(self) -> None:
        if self._on_silence is None:
            return
        left = None
        if (self._sentence and self._silence_bytes
                >= self._SILENCE_SHOWN_AFTER_S * _BYTES_PER_S
                and self._speech_bytes >= self.min_speech_s * _BYTES_PER_S):
            left = round(max(0.0, self.pause_s
                             - self._silence_bytes / _BYTES_PER_S), 1)
        if left != self._silence_shown:
            self._silence_shown = left
            self._on_silence(left)

    def _take(self, chunk: bytes) -> None:
        loud = self._detector.is_speech(chunk, bool(self._sentence))
        if not self._sentence:
            if not loud:
                self._remember_quiet(chunk)
                return
            # Speech begins: keep what came just before it - the first
            # consonant is quiet and "drei" otherwise arrives as "Reihe".
            for old in self._preroll:
                self._sentence += old
            self._preroll.clear()
            self._preroll_bytes = 0
            self._speech_bytes = 0
            self._silence_bytes = 0
            self._since_pass = 0
        if loud:
            if self._preroll:
                # Speaking again after a thinking pause: the same quiet
                # first consonant as at the start ("Pausen", not "hausen").
                for old in self._preroll:
                    self._sentence += old
                    self._since_pass += len(old)
                self._preroll.clear()
                self._preroll_bytes = 0
            self._speech_bytes += len(chunk)
            self._levels_now.append(rms16(chunk))
            self._silence_bytes = 0
        else:
            self._silence_bytes += len(chunk)
            if self._silence_bytes > self.keep_silence_s * _BYTES_PER_S:
                self._remember_quiet(chunk)   # a thinking pause: left out
                return
        self._sentence += chunk
        self._since_pass += len(chunk)

    def _remember_quiet(self, chunk: bytes) -> None:
        """Keep only the last moment of quiet, for when speech (re)starts."""
        self._preroll.append(chunk)
        self._preroll_bytes += len(chunk)
        while (self._preroll_bytes > self.preroll_s * _BYTES_PER_S
               and self._preroll):
            self._preroll_bytes -= len(self._preroll.popleft())

    def _decide(self, ending: bool) -> None:
        if not self._sentence:
            return
        paused = self._silence_bytes >= self.pause_s * _BYTES_PER_S
        # A sentence that runs too long is cut - at a thinking pause when
        # there is one, and only when there is none after half as long again.
        length = len(self._sentence) / _BYTES_PER_S
        at_breath = self._silence_bytes >= 0.3 * _BYTES_PER_S
        too_long = (length >= self.max_sentence_s and at_breath) or \
            length >= self.max_sentence_s * 1.5
        if ending or paused or too_long:
            self._finish("stop" if ending else "pause" if paused else "long")
        elif (self._since_pass >= self.step_s * _BYTES_PER_S
              and len(self._sentence) >= self.first_pass_s * _BYTES_PER_S):
            if self._is_background():
                self._since_pass = 0          # not you: show nothing
            else:
                self._pass()

    def sentence_level(self) -> float:
        """How loud the speech of the current sentence is.

        The loud parts count, not the middle: a short word like "ist" is
        mostly a soft "i" and a hiss, and its median sat so far below a
        normal sentence that it was taken for a voice from the next room.
        """
        if not self._levels_now:
            return 0.0
        ordered = sorted(self._levels_now)
        return ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))]

    def _is_background(self) -> bool:
        if not self.background_ratio or not self.voice_level:
            return False
        return self.sentence_level() < self.background_ratio * self.voice_level

    def _pass(self) -> None:
        self._since_pass = 0
        text = self._run_engine(bytes(self._sentence), final=False)
        if text is None:
            return
        settled, tail = self._agreement.update(text.split())
        self._on_update(" ".join(settled), " ".join(tail))

    def _finish(self, reason: str = "pause") -> None:
        audio = bytes(self._sentence)
        speech = self._speech_bytes
        level = self.sentence_level()
        background = self._is_background()
        self._sentence = bytearray()
        self._agreement.reset()
        self._since_pass = 0
        self._silence_bytes = 0
        self._speech_bytes = 0
        self._levels_now = []
        info = {"end": reason,
                "seconds": round(len(audio) / _BYTES_PER_S, 2),
                "speech": round(speech / _BYTES_PER_S, 2),
                "level": round(level), "voice": round(self.voice_level or 0)}
        if speech < self.min_speech_s * _BYTES_PER_S:
            self.last_finish = {**info, "result": "too little speech"}
            self._on_final("")           # a click or a cough: nothing to say
            return
        if background:
            self.last_finish = {**info, "result": "quieter than your voice"}
            self._on_final("")
            return
        text = None
        if (self.short_s and self._last_audio
                and speech < self.short_s * _BYTES_PER_S):
            text = self._with_context(audio)
            if text is not None:
                info["context"] = True
        if text is None:
            text = self._run_engine(audio, final=True)
        if text and not info.get("context"):
            self._last_audio = audio[-int(self.context_s * _BYTES_PER_S) // 2
                                     * 2:]
        if text is None:
            self.last_finish = {**info, "result": "recogniser failed"}
        else:
            self.last_finish = {**info, "result": "text" if text else "empty"}
            if text and level:
                self.voice_level = (level if not self.voice_level
                                    else 0.8 * self.voice_level + 0.2 * level)
        self._on_final(text or "")

    def _with_context(self, audio: bytes) -> str | None:
        """Recognise a short utterance behind the previous sentence and cut
        that sentence off again.  None when it cannot be done cleanly."""
        context = self._last_audio
        alone = self._run_engine(context, final=True)
        gap = b"\x00\x00" * int(0.3 * RATE)
        full = self._run_engine(context + gap + audio, final=True)
        if not alone or not full:
            return None
        return strip_context(full, alone)

    def _run_engine(self, audio: bytes, final: bool) -> str | None:
        started = time.perf_counter()
        try:
            text = self._transcribe(audio, final)
        except Exception as exc:
            if self._on_error is not None:
                self._on_error(exc)
            return None
        self.passes += 1
        self.pass_ms.append((time.perf_counter() - started) * 1000)
        return " ".join((text or "").split())

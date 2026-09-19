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


# ---------------------------------------------------------------------------
# The punctuation-only AI pass
# ---------------------------------------------------------------------------

def build_punctuation_prompt() -> str:
    """System instruction for the pass that may ONLY move punctuation."""
    return (
        "Du bist ein Korrektor für deutsche Zeichensetzung. Setze fehlende "
        "Kommas und korrigiere die Zeichensetzung nach den deutschen Regeln. "
        "Ändere KEIN einziges Wort: nicht die Formulierung, nicht die "
        "Reihenfolge, nicht die Schreibweise, nicht die Groß- und "
        "Kleinschreibung. Füge nichts hinzu und lasse nichts weg. Gib "
        "ausschließlich den Text zurück – ohne Anführungszeichen, ohne "
        "Kommentare."
    )


# Letters and digits only: everything between the words is punctuation, and
# punctuation is exactly what this pass is allowed to change.
_WORD_RE = re.compile(r"[^\W_]+")


def _words_only(text: str) -> list[str]:
    return _WORD_RE.findall(text or "")


def guard_punctuation(original: str, edited: str) -> str:
    """Accept the AI's punctuation only if it changed nothing else.

    The word sequence has to be identical, character for character, casing
    included.  If one word moved, changed spelling or case, or went missing,
    the answer is discarded and the dictation stands exactly as spoken.

    That strictness is the point.  ``guard_cleanup`` above only compares
    LENGTHS (0.5x to 1.8x), which a model can satisfy while rewriting every
    single word - fine for a pass that is asked to improve the wording, wrong
    for one that promised to touch nothing but the commas.
    """
    if not edited or not edited.strip():
        return original
    candidate = edited.strip().strip("\"'„“”").strip()
    if not candidate:
        return original
    if _words_only(candidate) != _words_only(original):
        return original
    return candidate


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
        # A lone "Ist." or "Hat." is an answer, not a question - a question
        # needs at least the verb and what it asks about.
        if (core.endswith(".") and not core.endswith("..")
                and first in _Q_OPENERS and len(core.split()) >= 2):
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
    "manchmal nie niemals überhaupt eben "
    # possessives
    "mein meine meinen meinem meiner meines dein deine deinen deinem "
    "deiner deines unser unsere unseren unserem unserer euer eure "
    # verb forms that are never nouns - a part that continues a sentence
    # starts with one all the time ("Auch bei diesem Diktat" + "Habe ich
    # ...").  No infinitives that double as nouns ("das Leben").
    "habe hast hat habt hatte hatten hätte hätten bin bist ist sind seid "
    "war warst waren wäre wären werde wirst wird würde würden kann kannst "
    "können könnte könnten muss musst müssen müsste sollte sollten soll "
    "sollst will willst wollte möchte möchtest möchten mag magst darf "
    "darfst gibt gab geht ging gehe mache machst macht sage sagst sagt "
    "schaue schaust schaut schau sieh siehe sehe siehst sieht lass lasse "
    "lässt nimm nehme nimmt gib gebe denke denkst denkt glaube glaubst "
    "glaubt weiß weißt finde findest findet brauche brauchst braucht hoffe "
    "komme kommst kommt"
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


# ---------------------------------------------------------------------------
# Commas German requires and Whisper does not write
# ---------------------------------------------------------------------------

# Measured against large-v3 on six dictated sentences: of the nine commas
# German grammar demands, Whisper writes four.  It handles "weil"/"wenn"
# clauses and reliably misses the comma before "aber", the one that closes an
# extended infinitive, and both commas around a relative clause.  The prompt
# makes no difference - that was tested too.
#
# Only unambiguous cases are repaired here.  A comma in the WRONG place is
# worse than a missing one, because it changes how the sentence reads, so
# relative clauses - which need real parsing to delimit - are deliberately
# left alone.  Every rule below has to survive its counter-examples in
# tests/test_dictation_commas.py.

# Punctuation that already ends a token, so nothing may be appended to it.
_ALREADY_PUNCTUATED = ",;:.!?…-–—("

# Words that can only ever open a new clause's SUBJECT.  This is what tells
# the conjunction "aber" ("..., aber er kam nicht") from the flavouring
# particle ("Das ist aber schön"), which takes no comma at all.
_SUBJECT_STARTERS = frozenset((
    "ich du er sie es wir ihr man "
    "das dies dieser diese dieses jener jene jenes "
    "der die den dem des ein eine einen einem einer eines "
    "mein meine meinen meinem meiner dein deine deinen deinem "
    "sein seine seinen ihr ihre ihren unser unsere euer eure "
    "kein keine keinen keinem "
    "da hier dort nichts niemand jemand alles alle jeder jede jedes"
).split())

# Finite auxiliaries and modals.  A flavouring particle sits directly after
# one of them ("ist aber", "hat aber", "war denn"), a conjunction does not -
# it follows the end of a clause.  Reuses the question openers, which are
# exactly that set of forms.
_PARTICLE_HOSTS = _Q_OPENERS

# Subordinating conjunctions that cannot be anything else, so a comma in front
# of them is safe whenever they are not the first word of the sentence.
_SUBORDINATORS = frozenset(
    "weil dass obwohl falls sodass ob wohingegen wenngleich".split())

# Conjunctions that are also particles or pronominal adverbs.  These need both
# tests: a subject must follow, and no finite auxiliary may precede.
_AMBIGUOUS_CONJUNCTIONS = frozenset("aber sondern denn jedoch damit".split())

# Verbs after which an EXPANDED infinitive takes a comma ("Ich habe versucht,
# dich zu erreichen").  A bare infinitive does not ("Ich habe versucht zu
# schlafen"), so at least one word has to stand between verb and "zu".
_INFINITIVE_TRIGGERS = frozenset((
    "versucht versuche versuchst versuchen versuchte versuchten "
    "beschlossen beschließe beschließt vorgeschlagen gebeten "
    "angefangen begonnen aufgehört geschafft vergessen versprochen "
    "gelernt geplant empfohlen erlaubt verboten vorgehabt gewagt"
).split())

_INFINITIVE_ENDINGS = ("en", "ern", "eln")


def _core(token: str) -> str:
    """A token reduced to its bare word, lower case."""
    return token.strip("\"'„“”»«().,;:!?…-–—").lower()


def _closes(token: str) -> bool:
    """True when a token already ends in punctuation."""
    stripped = token.rstrip("\"'„“”»«)")
    return bool(stripped) and stripped[-1] in _ALREADY_PUNCTUATED


def _infinitive_comma(cores: list[str], parts: list[str]) -> set[int]:
    """Indices before which an extended infinitive needs its opening comma."""
    marks: set[int] = set()
    for i, core in enumerate(cores):
        if core not in _INFINITIVE_TRIGGERS or _closes(parts[i]):
            continue
        for j in range(i + 2, min(len(cores), i + 8)):
            if _closes(parts[j - 1]):
                break                      # a clause boundary got there first
            if cores[j] != "zu":
                continue
            if j + 1 < len(cores) and cores[j + 1].endswith(_INFINITIVE_ENDINGS):
                marks.add(i + 1)           # comma goes after the trigger verb
            break
    return marks


def _commas_in_sentence(sentence: str) -> str:
    body = sentence.strip()
    if not body or " " not in body:
        return sentence
    lead = sentence[:len(sentence) - len(sentence.lstrip())]
    trail = sentence[len(lead) + len(body):]
    parts = body.split(" ")
    cores = [_core(p) for p in parts]

    marks: set[int] = set()
    for i, core in enumerate(cores):
        if i == 0 or _closes(parts[i - 1]):
            continue
        if core in _SUBORDINATORS:
            marks.add(i)
        elif core in _AMBIGUOUS_CONJUNCTIONS:
            following = cores[i + 1] if i + 1 < len(cores) else ""
            if (following in _SUBJECT_STARTERS
                    and cores[i - 1] not in _PARTICLE_HOSTS):
                marks.add(i)
    marks |= _infinitive_comma(cores, parts)

    for i in marks:
        if 0 < i <= len(parts) - 1 and not _closes(parts[i - 1]):
            parts[i - 1] += ","
    return lead + " ".join(parts) + trail


def fix_commas(text: str) -> str:
    """Insert the commas German requires that Whisper left out.

    Conservative by design: only conjunctions that cannot be read another way,
    plus the opening comma of an extended infinitive.  Never removes a comma,
    never touches one that is already there."""
    if not text or not text.strip():
        return text
    # Keep the separators, so paragraph breaks survive.
    tokens = re.split(r"((?<=[.!?…])\s+)", text)
    for i, tok in enumerate(tokens):
        if i % 2 == 0 and tok.strip():
            tokens[i] = _commas_in_sentence(tok)
    return "".join(tokens)


# ---------------------------------------------------------------------------
# Joining a new utterance onto text that is already there
# ---------------------------------------------------------------------------

# Characters after which a NEW sentence begins.
_SENTENCE_END = ".!?…"
# After these no space is wanted before the new text (an opening bracket or
# quote glues to what follows).
_NO_SPACE_AFTER = "([{„«\u201c'\"\u2013\u2014-/"


def match_case(previous: str, text: str) -> str:
    """``text`` with its first letter fitted to what stands before it.

    Whisper capitalises every utterance as if it were a sentence of its own.
    Dropped into a running sentence that is simply wrong, and it was wrong in
    one place for a long time: replacing a selection ("markiere den Nachricht",
    then speak the replacement) inserted the words verbatim, so "die Nachricht"
    arrived as "Die Nachricht" in the middle of the line.

    Only words German never capitalises mid-sentence are lowered, so a noun
    keeps its capital - "…und dann Haus" never becomes "haus".
    """
    if not text:
        return text
    tail = (previous or "").rstrip("\r")
    stripped = tail.rstrip()
    if not stripped:
        return text                         # nothing before: leave as spoken
    # A line break starts a new sentence just as a full stop does - and it is
    # removed by rstrip(), so it has to be read off the UNstripped tail.
    starts_sentence = (tail.endswith(("\n", "\r"))
                       or stripped[-1] in _SENTENCE_END)
    head, rest = text[:1], text[1:]
    if starts_sentence:
        return head.upper() + rest
    if head.isupper():
        first_word = re.split(r"[\s,;:.!?]", text, 1)[0]
        if first_word.lower() in _LOWER_WORDS:
            return head.lower() + rest      # only the safe function words
    return text


def join_dictation(previous: str, new_text: str,
                   following: str = "") -> str:
    """Return ``new_text`` prepared to be inserted between ``previous`` and
    ``following``.

    Whisper transcribes every utterance as if it were a sentence of its own:
    capital first letter, no leading space.  Appended to existing text that is
    exactly wrong – "…und dann Das war gut." instead of "…und dann das war
    gut."  This decides both details from what comes immediately before:

    * a separator space unless the text already ends in one (or in a character
      that glues, like an opening bracket);
    * capitalisation – a capital first letter after ``.``/``!``/``?`` or at the
      very beginning, otherwise lower case, but ONLY for words German never
      capitalises mid-sentence (``_LOWER_WORDS``).  A noun keeps its capital,
      so "…und dann Haus" never becomes "haus".

    ``following`` is what stands AFTER the insertion point.  When a word is
    dictated into the middle of a running sentence, Whisper's trailing full
    stop would land inside that sentence ("das ist sehr. gut"), so it is
    dropped – but only there: a complete sentence inserted between two others
    keeps its punctuation.

    ``previous`` may be the whole buffer or just its last few characters; only
    the tail is looked at.  The same goes for ``following`` and its head.
    """
    if not new_text:
        return ""
    tail = (previous or "").rstrip("\r")
    stripped = tail.rstrip()
    if not stripped:
        return new_text                     # nothing before: leave as spoken

    last = stripped[-1]
    # A line break starts a new sentence just as a full stop does – but it is
    # removed by rstrip(), so it has to be read off the UNstripped tail.
    starts_sentence = tail.endswith(("\n", "\r")) or last in _SENTENCE_END
    # A line break already separates; anything else may need a space.
    if tail.endswith(("\n", " ", "\t")) or last in _NO_SPACE_AFTER:
        sep = ""
    else:
        sep = " "
    # "\u201c" opens an English quote but CLOSES a German one („…“): after a
    # German pair it is a closing mark, and the next word needs its space.
    if last == "\u201c" and stripped.count("\u201e") >= stripped.count("\u201c"):
        sep = " "

    if not starts_sentence and following.strip():
        # Mid-sentence insertion: the sentence continues after us, so the full
        # stop Whisper appends to every utterance would end it in the wrong
        # place.  Only stripped while CONTINUING a sentence – a whole sentence
        # dictated between two others (previous ends in „.") keeps its own.
        new_text = new_text.rstrip().rstrip(_SENTENCE_END)
        if not new_text:
            return ""

    return sep + match_case(previous, new_text)


# ---------------------------------------------------------------------------
# Spoken dates → numeric dates
# ---------------------------------------------------------------------------

_MONTHS = {
    "januar": 1, "jan": 1, "jaenner": 1, "jänner": 1,
    "februar": 2, "feb": 2, "februa": 2,
    "maerz": 3, "märz": 3, "mrz": 3, "mar": 3,
    "april": 4, "apr": 4,
    "mai": 5,
    "juni": 6, "jun": 6,
    "juli": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "oktober": 10, "okt": 10,
    "november": 11, "nov": 11,
    "dezember": 12, "dez": 12,
}

# Spoken ordinals for the day.  Whisper writes a dictated ordinal sometimes as
# "20." and sometimes in words, so both have to be understood.
_ORDINAL_UNITS = {
    "erst": 1, "zweit": 2, "dritt": 3, "viert": 4, "fuenft": 5, "fünft": 5,
    "sechst": 6, "siebt": 7, "siebent": 7, "acht": 8, "neunt": 9, "zehnt": 10,
    "elft": 11, "zwoelft": 12, "zwölft": 12, "dreizehnt": 13, "vierzehnt": 14,
    "fuenfzehnt": 15, "fünfzehnt": 15, "sechzehnt": 16, "siebzehnt": 17,
    "achtzehnt": 18, "neunzehnt": 19, "zwanzigst": 20,
    "einundzwanzigst": 21, "zweiundzwanzigst": 22, "dreiundzwanzigst": 23,
    "vierundzwanzigst": 24, "fuenfundzwanzigst": 25, "fünfundzwanzigst": 25,
    "sechsundzwanzigst": 26, "siebenundzwanzigst": 27, "achtundzwanzigst": 28,
    "neunundzwanzigst": 29, "dreissigst": 30, "dreißigst": 30,
    "einunddreissigst": 31, "einunddreißigst": 31,
}
# "zwanzigsten", "zwanzigster", "zwanzigste" – any of the usual endings.
_ORDINAL_WORDS = {stem + end: value
                  for stem, value in _ORDINAL_UNITS.items()
                  for end in ("e", "en", "er", "em", "es")}

_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ORD_RE = "|".join(sorted(_ORDINAL_WORDS, key=len, reverse=True))

# "20. August 2026" / "20 August 2026" / "zwanzigsten August 2026"
_DATE_RE = re.compile(
    r"\b(?:(\d{1,2})\.?|(" + _ORD_RE + r"))\s+(" + _MONTH_RE + r")\.?"
    r"(?:\s+(\d{4})|\s+(\d{2})(?![\d.]))?\b",
    re.IGNORECASE)


# Digits said one by one: "eins, acht, acht, sieben" is the number 1887 (a
# year, a PIN, a phone number): said as single digits in a row, with only
# commas or spaces between them.  Four or more: "drei, vier, fünf Mal" is
# a guess at a number of times, not a number.
_DIGIT_WORDS = {"null": "0", "eins": "1", "zwei": "2", "zwo": "2",
                "drei": "3", "vier": "4", "fünf": "5", "fuenf": "5",
                "sechs": "6", "sieben": "7", "acht": "8", "neun": "9"}
_DIGIT_RUN = re.compile(
    r"\b(?:" + "|".join(_DIGIT_WORDS) + r")\b"
    r"(?:[ \t]*[,\-]?[ \t]+(?:" + "|".join(_DIGIT_WORDS) + r")\b"
    r"|[ \t]*,(?:" + "|".join(_DIGIT_WORDS) + r")\b){3,}",
    re.IGNORECASE)


def fix_digit_sequences(text: str) -> str:
    """"Eins, acht, acht, sieben." -> "1887." """
    def join(m: re.Match) -> str:
        words = re.findall(r"[a-zäöüß]+", m.group(0), re.IGNORECASE)
        return "".join(_DIGIT_WORDS[w.lower()] for w in words)
    return _DIGIT_RUN.sub(join, text or "")


def fix_dates(text: str) -> str:
    """Write spoken dates as numbers: "20. August 2026" → "20.08.2026".

    Dictating a date is one of the places where speech and writing differ most:
    people SAY "zwanzigster August zweitausendsechsundzwanzig" and want to READ
    "20.08.2026".  Handles the digit form and the spelled-out ordinal, with or
    without a year; a two-digit year is expanded to 20xx.

    Deliberately conservative: only a real month name triggers it, an
    impossible day is left untouched, and nothing else in the sentence is
    changed.  Running this twice is safe.

    A date the recogniser already wrote in digits gets two-digit day and
    month too: "6.10.2026" -> "06.10.2026".  Without a year only after a
    word that introduces a date ("am 6.10."), so a chapter "6.10." stays;
    a time ("6.10 Uhr") has no dot after the month and is never touched.
    """
    if not text:
        return text

    def repl(m: re.Match) -> str:
        digits, word, month_name, year4, year2 = m.groups()
        if digits is not None:
            day = int(digits)
        else:
            day = _ORDINAL_WORDS.get(word.lower(), 0)
        month = _MONTHS.get(month_name.lower().rstrip("."), 0)
        if not (1 <= day <= 31) or not month:
            return m.group(0)           # not a date after all – leave it alone
        out = f"{day:02d}.{month:02d}."
        if year4:
            out += year4
        elif year2:
            out += f"20{year2}"
        elif m.string[m.end():m.end() + 1] == ".":
            # A date without a year already ends in a dot; at the end of a
            # sentence the sentence's own period would double it ("31.12..").
            # German merges the two, so drop ours and let the sentence keep its.
            out = out[:-1]
        return out

    return _pad_numeric_dates(_DATE_RE.sub(repl, text))


_NUMERIC_DATE_RE = re.compile(
    r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?![\d.])")
_NUMERIC_DAY_RE = re.compile(
    r"\b(am|vom|bis|zum|ab|seit|bis zum|den|dem)(\s+)(\d{1,2})\.(\d{1,2})\."
    r"(?![\d])", re.IGNORECASE)


def _pad_numeric_dates(text: str) -> str:
    def full(m: re.Match) -> str:
        day, month = int(m.group(1)), int(m.group(2))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return m.group(0)
        return f"{day:02d}.{month:02d}.{m.group(3)}"

    def short(m: re.Match) -> str:
        day, month = int(m.group(3)), int(m.group(4))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{day:02d}.{month:02d}."

    return _NUMERIC_DAY_RE.sub(short, _NUMERIC_DATE_RE.sub(full, text))

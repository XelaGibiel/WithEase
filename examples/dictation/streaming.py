"""Cutting a running recording into parts at the pauses.

The microphone stays on; every longer pause hands what was said so far to
the recogniser, and the next part begins.  A speech detector (Silero, or a
plain level gate as a fallback) decides what is speech, so keyboard clicks
and room noise do not start a part, and long thinking pauses are left out
of what the recogniser hears.

``StreamSession`` can also run interim passes over the part being spoken
(``step_s``) and settle words that two passes agree on - the method from
*Turning Whisper into a Real-Time Transcription System* (Macháček et al.
2023).  Normal dictation switches that off and recognises each part once.

A recogniser is any function ``transcribe(pcm, final) -> str`` for 16 kHz
mono int16, which keeps this file testable without a model.
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


# Frequent German words: "Aussprache anlernen" never keeps one of these as
# a heard variant - it would replace the real word everywhere.
_DE = frozenset("""der die das und oder aber ist sind war waren ein eine einen
es ich wir sie er du mein dein nicht mit auf für von zu im in den dem des
auch noch schon wie was warum wo wann kann könnte würde soll wird nur so
wenn hier alle nein ja bitte danke hallo""".split())


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


SENTENCE_MARKS = ".,;:!?"


def mark_replaces(previous: str) -> int:
    """How many characters at the end of ``previous`` a spoken mark takes
    the place of: the spaces, and the recogniser's own mark before them.

    "... benutzen kann." + a spoken "?" is "... benutzen kann?" - the full
    stop was only the recogniser's guess at the end of a part.  "z.B.", a
    date ("16.") and "..." keep their dot."""
    stripped = (previous or "").rstrip(" ")
    spaces = len(previous or "") - len(stripped)
    last = stripped[-1:]
    if not last or last not in SENTENCE_MARKS or stripped.endswith(".."):
        return spaces
    if last == ".":
        before = re.search(r"(\S+)\.$", stripped)
        word = before.group(1).lower() if before else ""
        if (word in _ABBREVIATIONS or word.rstrip(".") in _ABBREVIATIONS
                or word[-1:].isdigit()):
            return spaces
    return spaces + 1


def continue_after_pause(previous: str, text: str) -> tuple[int, str] | None:
    """A new part that continues the sentence before it.

    Every part the recogniser hears ends with a full stop, also when the
    pause was only for thinking: "... zwischen einem Satz lasse." + "Damit
    auch ...".  When the new part starts with a word that does not open a
    sentence, that full stop goes: returns ``(characters to remove at the
    end of previous, text to insert instead)`` - here ``(1, ", damit auch
    ...")``.  None when nothing is to be joined."""
    stripped = (previous or "").rstrip(" ")
    if not stripped.endswith(".") or stripped.endswith(".."):
        return None
    before = re.search(r"(\S+)\.$", stripped)
    if before is None:
        return None
    last = before.group(1).lower()
    # "z.B." or a date ("16.") end in a dot that is not a sentence end
    if (last in _ABBREVIATIONS or last.rstrip(".") in _ABBREVIATIONS
            or last[-1:].isdigit()):
        return None
    match = re.match(r"([A-ZÄÖÜ][a-zäöüß]+)\b(.*)", (text or "").strip(), re.S)
    if match is None or not continues(match.group(1), match.group(2)):
        return None
    word = match.group(1).lower()
    cut = len(previous) - len(stripped) + 1
    return cut, (" " if word in NO_COMMA else ", ") + word + match.group(2)


# -- the dictionary ------------------------------------------------------------

def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text or "").casefold()


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
                 on_error: Callable[[Exception], None] | None = None,
                 on_silence: Callable[[float | None], None] | None = None,
                 preview_after_s: float = 0.0,
                 on_preview: Callable[[str], None] | None = None,
                 on_skipped: Callable[[bytes, str], None] | None = None,
                 ) -> None:
        # ``on_skipped(audio, reason)``: a part thrown away before the
        # recogniser saw it ("quieter than your voice") - kept by the caller
        # so a wrongly dropped part can still be looked at.
        self._on_skipped = on_skipped
        self._transcribe = transcribe
        # ``on_silence(seconds left)`` while you are quiet in the middle of a
        # sentence: how long until the pause ends it.  None when that is
        # over (you speak again, or the sentence is finished).
        self._on_silence = on_silence
        self._silence_shown: float | None = None
        # A look at the part as soon as you have been quiet this long -
        # ``transcribe(pcm, final=False)``, answered with ``on_preview(text)``
        # (e.g. to show whether a command or dictation is coming; answering
        # True ends the part right there).  When you
        # stay quiet the part ends with exactly this audio, and the preview
        # IS the result: ``transcribe`` gets the same audio once more and
        # can answer from what it remembered.
        self.preview_after_s = preview_after_s
        self._on_preview = on_preview
        self._previewed = -1            # length of the part last previewed
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
        # no pass on less audio than this
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
            self._maybe_preview()
            self._report_silence()
        self._silence_shown = None
        if self._on_silence is not None:
            self._on_silence(None)

    def _maybe_preview(self) -> None:
        if (self._on_preview is None or not self.preview_after_s
                or not self._sentence
                or len(self._sentence) == self._previewed
                or self._silence_bytes < self.preview_after_s * _BYTES_PER_S
                or self._speech_bytes < self.min_speech_s * _BYTES_PER_S
                or self._is_background()):
            return
        self._previewed = len(self._sentence)
        text = self._run_engine(bytes(self._sentence), final=False)
        # ``on_preview`` answers True when the part need not wait for the
        # rest of the pause (a command: it runs at once)
        if text is not None and self._on_preview(text):
            self._finish("early")

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
        self._previewed = -1
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
            if self._on_skipped is not None:
                self._on_skipped(audio, "quieter than your voice")
            self._on_final("")
            return
        text = self._run_engine(audio, final=True)
        if text is None:
            self.last_finish = {**info, "result": "recogniser failed"}
        else:
            self.last_finish = {**info, "result": "text" if text else "empty"}
            if text and level:
                self.voice_level = (level if not self.voice_level
                                    else 0.8 * self.voice_level + 0.2 * level)
        self._on_final(text or "")

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

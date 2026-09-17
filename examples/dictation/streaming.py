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
                 preroll_s: float = 0.3, max_sentence_s: float = 25.0,
                 min_speech_s: float = 0.15, gate: float = 0.0,
                 first_pass_s: float = 0.0,
                 on_error: Callable[[Exception], None] | None = None) -> None:
        self._transcribe = transcribe
        self._on_update = on_update
        self._on_final = on_final
        self._on_error = on_error
        self.step_s = step_s
        self.pause_s = pause_s
        self.preroll_s = preroll_s
        self.max_sentence_s = max_sentence_s
        self.min_speech_s = min_speech_s
        # Parakeet picks the language by itself and guesses English on the
        # first half second ("Constant." for "Kannst"); wait for more audio.
        self.first_pass_s = first_pass_s
        self._levels = Levels(gate)
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
            self._decide(ending)

    def _take(self, chunk: bytes) -> None:
        loud = self._levels.is_speech(rms16(chunk))
        if not self._sentence:
            if not loud:
                self._preroll.append(chunk)
                self._preroll_bytes += len(chunk)
                while (self._preroll_bytes > self.preroll_s * _BYTES_PER_S
                       and self._preroll):
                    self._preroll_bytes -= len(self._preroll.popleft())
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
        self._sentence += chunk
        self._since_pass += len(chunk)
        if loud:
            self._speech_bytes += len(chunk)
            self._silence_bytes = 0
        else:
            self._silence_bytes += len(chunk)

    def _decide(self, ending: bool) -> None:
        if not self._sentence:
            return
        paused = self._silence_bytes >= self.pause_s * _BYTES_PER_S
        too_long = len(self._sentence) >= self.max_sentence_s * _BYTES_PER_S
        if ending or paused or too_long:
            self._finish()
        elif (self._since_pass >= self.step_s * _BYTES_PER_S
              and len(self._sentence) >= self.first_pass_s * _BYTES_PER_S):
            self._pass()

    def _pass(self) -> None:
        self._since_pass = 0
        text = self._run_engine(bytes(self._sentence), final=False)
        if text is None:
            return
        settled, tail = self._agreement.update(text.split())
        self._on_update(" ".join(settled), " ".join(tail))

    def _finish(self) -> None:
        audio = bytes(self._sentence)
        speech = self._speech_bytes
        self._sentence = bytearray()
        self._agreement.reset()
        self._since_pass = 0
        self._silence_bytes = 0
        self._speech_bytes = 0
        if speech < self.min_speech_s * _BYTES_PER_S:
            self._on_final("")           # a click or a cough: nothing to say
            return
        text = self._run_engine(audio, final=True)
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

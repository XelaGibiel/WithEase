"""The stop key's click is cut off the end of a recording - the word is not.

The old rule dropped a fixed quarter second.  Measured against large-v3 with
"drei" and the key released 0.15 s after the word: that cut took the end of
the word with it, what was left no longer passed the gate, and nothing was
transcribed - although Whisper recognised the whole recording as "3".  Short
words are exactly the ones spoken and released quickly.
"""
import io
import math
import os
import struct
import sys
import wave

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtWidgets import QApplication  # noqa: E402

RATE = 16000


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def m(app):
    import module as dic
    mod = dic.DictationModule()
    mod._rec_rate, mod._rec_channels = RATE, 1
    return mod


def pcm(parts):
    """parts: (seconds, peak) - a 220 Hz tone at that level, 0 = silence."""
    out = bytearray()
    n = 0
    for seconds, peak in parts:
        for _ in range(int(seconds * RATE)):
            n += 1
            out += struct.pack("<h", int(peak * 32767 * math.sin(2 * math.pi * 220 * n / RATE)))
    return bytes(out)


def secs(raw):
    return len(raw) / 2 / RATE


def as_wav(raw):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(raw)
    return buf.getvalue()


def test_a_quick_release_keeps_the_whole_word(m):
    """The measured failure: 0.31 s word, key up 0.15 s later."""
    raw = pcm([(0.40, 0), (0.31, 0.5), (0.15, 0)])
    trimmed = m._trim_key_click(raw, RATE, 1)
    assert secs(trimmed) >= 0.71, "the word ended at 0.71 s and must be there"


def test_the_short_word_then_passes_the_gate(m):
    raw = pcm([(0.40, 0), (0.31, 0.5), (0.15, 0)])
    assert m._holds_speech(as_wav(m._trim_key_click(raw, RATE, 1)))


def test_a_click_after_the_word_is_cut(m):
    raw = pcm([(0.20, 0), (0.50, 0.5), (0.15, 0), (0.02, 0.9), (0.03, 0)])
    trimmed = m._trim_key_click(raw, RATE, 1)
    assert secs(trimmed) <= 0.20 + 0.50 + 0.06, "the click is still there"
    assert secs(trimmed) >= 0.70, "but the word is not"


def test_a_loud_click_does_not_make_the_word_look_like_silence(m):
    """The level is judged BEFORE the tail: a click far louder than the
    speech must not push the word under the bar."""
    raw = pcm([(0.20, 0), (0.50, 0.08), (0.10, 0), (0.02, 1.0)])
    trimmed = m._trim_key_click(raw, RATE, 1)
    assert secs(trimmed) >= 0.70


def test_speech_running_to_the_very_end_is_left_alone(m):
    """Key released mid-word: there is nothing to cut."""
    raw = pcm([(0.20, 0), (0.80, 0.5)])
    assert m._trim_key_click(raw, RATE, 1) == raw


def test_at_most_a_quarter_second_is_ever_removed(m):
    raw = pcm([(0.30, 0.5), (1.00, 0)])
    trimmed = m._trim_key_click(raw, RATE, 1)
    assert secs(raw) - secs(trimmed) <= 0.25 + 0.01


def test_sound_is_measured_for_the_log(m):
    seconds, sound = m._sound_seconds(as_wav(pcm([(0.20, 0), (0.30, 0.5), (0.20, 0)])))
    assert seconds == pytest.approx(0.70, abs=0.02)
    assert sound == pytest.approx(0.30, abs=0.03)

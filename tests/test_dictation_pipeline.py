"""What reaches the model, and what may change the text afterwards.

Two promises are pinned down here:

* A short word must not be thrown away before Whisper ever sees it.  The old
  rule dropped every recording under 0.4 s - and measured against large-v3,
  "ja" (0.31 s), "zurück" (0.31 s) and "nimm" (0.37 s) were each recognised
  perfectly once the model was allowed to look at them.

* The punctuation pass may move punctuation and nothing else.  A dictation
  that quietly says something different from what was spoken is worse than
  one with a missing comma, so the check is exact rather than approximate.
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

from postprocess import guard_punctuation  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# -- audio helpers -----------------------------------------------------------

def wav_of(parts, rate=16000, channels=1):
    """parts: list of (seconds, peak 0..1) - a tone at that level."""
    frames = bytearray()
    phase = 0
    for seconds, peak in parts:
        for _ in range(int(seconds * rate)):
            phase += 1
            value = int(peak * 32767 * math.sin(phase * 2 * math.pi * 220 / rate))
            frames += struct.pack("<h", value) * channels
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


# -- the gate ----------------------------------------------------------------

def test_a_short_word_reaches_the_model(app):
    """0.31 s of speech: what "ja" and "zurück" actually measure."""
    import module as dic
    m = dic.DictationModule()
    clip = wav_of([(0.05, 0.0), (0.31, 0.4), (0.10, 0.0)])
    assert m._holds_speech(clip), "a spoken word must not be discarded"


def test_an_even_shorter_word_still_reaches_it(app):
    import module as dic
    m = dic.DictationModule()
    assert m._holds_speech(wav_of([(0.03, 0.0), (0.16, 0.5), (0.04, 0.0)]))


def test_a_stray_key_tap_is_still_rejected(app):
    """The gate exists for a reason: a 20 ms click is not a word, and Whisper
    turns one into an invented word."""
    import module as dic
    m = dic.DictationModule()
    assert not m._holds_speech(wav_of([(0.10, 0.0), (0.02, 0.6), (0.10, 0.0)]))


def test_pure_silence_is_rejected(app):
    import module as dic
    m = dic.DictationModule()
    assert not m._holds_speech(wav_of([(1.5, 0.0)]))


def test_a_very_short_recording_is_rejected(app):
    import module as dic
    m = dic.DictationModule()
    assert not m._holds_speech(wav_of([(0.10, 0.5)]))


def test_a_quiet_far_field_recording_is_judged_on_its_own_scale(app):
    """A webcam microphone peaks around 0.08.  Judging it by an absolute
    threshold would throw away everything such a microphone ever records."""
    import module as dic
    m = dic.DictationModule()
    m._rec_rate, m._rec_channels = 16000, 1
    assert m._holds_speech(wav_of([(0.05, 0.0), (0.35, 0.06), (0.10, 0.0)]))


def test_the_gate_counts_frames_not_bytes_at_other_rates(app):
    """The old byte limit (8000) meant 250 ms at 16 kHz mono but only 42 ms at
    44.1 kHz stereo - the fallback format when a microphone cannot do 16 kHz."""
    import module as dic
    m = dic.DictationModule()
    m._rec_rate, m._rec_channels = 44100, 2
    clip = wav_of([(0.05, 0.0), (0.31, 0.4), (0.10, 0.0)],
                  rate=44100, channels=2)
    assert m._holds_speech(clip)


# -- clip length is read from the file, not assumed --------------------------

def test_clip_length_follows_the_recording_format():
    import module as dic
    one_second = wav_of([(1.0, 0.3)], rate=44100, channels=1)
    assert dic._clip_seconds(one_second) == pytest.approx(1.0, abs=0.01)


def test_clip_length_at_the_usual_rate():
    import module as dic
    assert dic._clip_seconds(wav_of([(2.0, 0.3)])) == pytest.approx(2.0, abs=0.01)


# -- the punctuation guard ---------------------------------------------------

def test_added_commas_are_accepted():
    before = "Ich wollte anrufen aber du warst nicht da"
    after = "Ich wollte anrufen, aber du warst nicht da."
    assert guard_punctuation(before, after) == after


@pytest.mark.parametrize("edited", [
    "Ich wollte dich anrufen, aber du warst nicht da.",   # a word added
    "Ich wollte anrufen, aber du warst nicht hier.",      # a word swapped
    "Ich wollte anrufen, aber du warst nicht.",           # a word dropped
    "Ich Wollte anrufen, aber du warst nicht da.",        # casing changed
    "Ich wolte anrufen, aber du warst nicht da.",         # spelling changed
    "Du warst nicht da, aber ich wollte anrufen.",        # order changed
    "",
    "   ",
])
def test_anything_but_punctuation_is_rejected(edited):
    before = "Ich wollte anrufen aber du warst nicht da"
    assert guard_punctuation(before, edited) == before


def test_surrounding_quotes_from_a_chatty_model_are_stripped():
    before = "Ich komme später weil es regnet"
    assert guard_punctuation(before, '"Ich komme später, weil es regnet."') \
        == "Ich komme später, weil es regnet."


def test_the_guard_is_stricter_than_the_cleanup_guard():
    """guard_cleanup only compares lengths, so a full rewrite passes it.  The
    punctuation pass promises more than that, so it has to check more."""
    from postprocess import guard_cleanup
    before = "Ich wollte anrufen aber du warst nicht da"
    rewritten = "Ich habe versucht dich zu erreichen doch niemand war daheim"
    assert guard_cleanup(before, rewritten) == rewritten     # slips through
    assert guard_punctuation(before, rewritten) == before     # caught

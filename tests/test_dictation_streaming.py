"""Cutting a running recording at the pauses: the session, the speech
detector and the window while the microphone still runs.

No model: a fake recogniser returns what a real one would say after so many
seconds of audio.
"""
import math
import os
import struct
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

import streaming as st  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _cursor_to_end(win):
    cursor = win._edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    win._edit.setTextCursor(cursor)


def test_a_word_settles_when_two_passes_agree():
    a = st.Agreement()
    assert a.update("Kannst du".split()) == ([], ["Kannst", "du"])
    settled, tail = a.update("Kannst du mir".split())
    assert settled == ["Kannst", "du"] and tail == ["mir"]

def test_a_settled_word_never_jumps_back():
    a = st.Agreement()
    a.update("Kannst du mir".split())
    a.update("Kannst du mir bei".split())
    settled, tail = a.update("Kannst du wir bei Gelegenheit".split())
    assert settled == ["Kannst", "du", "mir"]
    assert tail == ["bei", "Gelegenheit"]

def test_a_comma_that_comes_later_still_arrives():
    a = st.Agreement()
    a.update("die Rechnung raussuchen die".split())
    a.update("die Rechnung raussuchen die wir".split())
    settled, _tail = a.update("die Rechnung raussuchen, die wir letzte".split())
    assert settled[2] == "raussuchen,"

def test_reset_starts_a_new_sentence():
    a = st.Agreement()
    a.update(["Hallo", "Welt"])
    a.update(["Hallo", "Welt"])
    a.reset()
    assert a.update(["Neu"]) == ([], ["Neu"])

def _tone(seconds, amplitude=6000):
    n = int(seconds * st.RATE)
    return b"".join(struct.pack("<h", int(amplitude * math.sin(i / 5)))
                    for i in range(n))

def _silence(seconds, level=40):
    n = int(seconds * st.RATE)
    return b"".join(struct.pack("<h", level if i % 2 else -level)
                    for i in range(n))

class _Recogniser:
    """Says one more word per half second of audio."""
    WORDS = "Kannst du mir bei Gelegenheit die Rechnung raussuchen".split()

    def __init__(self):
        self.calls = []

    def __call__(self, pcm, final):
        seconds = len(pcm) / (st.RATE * 2)
        self.calls.append((round(seconds, 2), final))
        n = min(len(self.WORDS), int(seconds / 0.5))
        text = " ".join(self.WORDS[:n])
        return text + ("." if final and text else "")

def _run(session, audio, chunk_s=0.1):
    step = int(chunk_s * st.RATE) * 2
    for i in range(0, len(audio), step):
        session._take(audio[i:i + step])
        session._decide(False)

def _session(recogniser, updates, finals, **kw):
    return st.StreamSession(recogniser, lambda s, t: updates.append((s, t)),
                            finals.append, **kw)

def test_text_grows_while_speaking_and_finishes_after_a_pause():
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals)
    _run(s, _silence(0.5) + _tone(3.0) + _silence(1.2))

    assert len(updates) >= 4, "a pass every half second"
    assert any(settled for settled, _tail in updates), "words settle"
    assert finals and finals[-1].endswith("."), "the sentence is finished"
    assert rec.calls[-1][1] is True

def test_silence_alone_produces_nothing():
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals)
    _run(s, _silence(3.0))
    assert not rec.calls and not updates and not finals

def test_the_start_of_a_word_is_kept():
    """Speech is detected a moment after it began; the quiet first consonant
    must still reach the recogniser."""
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals, preroll_s=0.3)
    _run(s, _silence(1.0) + _tone(1.0) + _silence(1.2))
    total = rec.calls[-1][0]
    assert total >= 1.0 + 0.25, f"only {total} s reached the recogniser"

def test_a_click_is_not_a_sentence():
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals, min_speech_s=0.15)
    _run(s, _silence(0.5) + _tone(0.05) + _silence(1.5), chunk_s=0.05)
    assert finals == [""]
    assert not any(final for _sec, final in rec.calls)

def test_stopping_finishes_the_open_sentence():
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals)
    s.start()
    s.put(_silence(0.3) + _tone(2.0))
    s.stop()
    assert finals and finals[-1]

def test_a_failing_pass_does_not_end_the_session():
    errors, finals = [], []
    calls = {"n": 0}

    def flaky(pcm, final):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("busy")
        return "Hallo"
    s = st.StreamSession(flaky, lambda *_: None, finals.append,
                         on_error=errors.append)
    _run(s, _tone(2.0) + _silence(1.2))
    assert errors and finals == ["Hallo"]

def test_a_very_long_sentence_is_cut():
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals, max_sentence_s=4.0)
    _run(s, _tone(13.0))
    assert len(finals) >= 2
    assert s.last_finish["end"] == "long"

class _FakeVad:
    """Returns a fixed probability per 512-sample window."""

    def __init__(self, prob):
        self.prob = prob
        self.lengths = []

    def __call__(self, audio):
        import numpy as np
        assert len(audio) % 512 == 0
        self.lengths.append(len(audio))
        return np.full((len(audio) // 512, 1), self.prob, dtype="float32")

def test_the_speech_gate_needs_clear_speech_to_start_but_less_to_go_on():
    vad = _FakeVad(0.4)
    gate = st.SileroGate(vad, start=0.5, keep=0.35)
    chunk = b"\x01\x00" * 1600
    assert gate.is_speech(chunk, in_speech=False) is False
    assert gate.is_speech(chunk, in_speech=True) is True

def test_the_speech_gate_keeps_a_short_history_in_whole_windows():
    vad = _FakeVad(0.9)
    gate = st.SileroGate(vad)
    for _ in range(40):
        gate.is_speech(b"\x01\x00" * 700)
    assert max(vad.lengths) <= st.SileroGate.HISTORY * 512

def test_a_noise_the_gate_rejects_starts_no_sentence():
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals, detector=st.SileroGate(_FakeVad(0.1)))
    _run(s, _tone(3.0) + _silence(1.2))
    assert not rec.calls and not finals

def test_without_the_speech_model_loudness_is_used(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_vad(name, *args, **kwargs):
        if name == "faster_whisper.vad":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_vad)
    assert isinstance(st.make_gate(), st.EnergyGate)

def test_a_quiet_voice_from_the_next_room_is_ignored():
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals, background_ratio=0.3)
    _run(s, _silence(0.3) + _tone(2.0, amplitude=8000) + _silence(1.2))
    assert finals and finals[-1], "your own sentence becomes text"
    assert s.voice_level

    calls_before = len(rec.calls)
    _run(s, _silence(0.3) + _tone(2.0, amplitude=1200) + _silence(1.2))
    assert finals[-1] == "", "a voice far quieter than yours does not"
    assert s.last_finish["result"] == "quieter than your voice"
    assert len(rec.calls) == calls_before, "and it is never shown"

def test_without_a_learned_voice_every_sentence_counts():
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals, background_ratio=0.3)
    _run(s, _silence(0.3) + _tone(2.0, amplitude=1200) + _silence(1.2))
    assert finals[-1]

def test_each_sentence_leaves_a_reason_for_the_log():
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals)
    _run(s, _silence(0.3) + _tone(2.0) + _silence(1.2))
    assert s.last_finish["result"] == "text"
    assert s.last_finish["speech"] > 1.5

def test_a_thinking_pause_leaves_the_sentence_open():
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals, pause_s=2.5)
    _run(s, _silence(0.3) + _tone(1.5) + _silence(1.5) + _tone(1.5))
    assert not finals, "1.5 s of thinking is not the end of the sentence"
    _run(s, _silence(2.6))
    assert len(finals) == 1

def test_the_recogniser_does_not_hear_the_thinking_pause():
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals, pause_s=5.0, keep_silence_s=0.3)
    _run(s, _silence(0.3) + _tone(1.0) + _silence(3.0) + _tone(1.0))
    s._finish("stop")
    heard = rec.calls[-1][0]
    # 3 s of pause shrink to 0.3 s kept + 0.3 s lead-in before the next word
    assert heard < 0.3 + 1.0 + 0.3 + 0.3 + 1.0 + 0.2, f"{heard} s - pause kept"

def test_a_long_sentence_is_cut_at_a_breath():
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals, pause_s=5.0, max_sentence_s=4.0)
    _run(s, _tone(4.5))
    assert not finals, "no cut in the middle of a word"
    _run(s, _silence(0.4))
    assert finals and s.last_finish["end"] == "long"

def test_a_short_loud_word_is_not_taken_for_a_background_voice():
    """"ist" is mostly a soft vowel and a hiss; its loud part decides."""
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals, background_ratio=0.25, voice_level=6000)
    word = _tone(0.1, amplitude=7000) + _tone(0.4, amplitude=900)
    _run(s, _silence(0.3) + word + _silence(1.2), chunk_s=0.05)
    assert s.last_finish["result"] != "quieter than your voice"

def test_the_misheard_quotation_word_works():
    import commands_de as cde
    got = cde.apply_inline_punctuation(
        "Er sagte Anführerstriche unten Hallo Anführerstriche oben und ging.")
    assert got == "Er sagte „Hallo“ und ging."

def test_a_lone_verb_is_no_question():
    from postprocess import fix_question_marks
    assert fix_question_marks("Ist.") == "Ist."
    assert fix_question_marks("Hast du Zeit.") == "Hast du Zeit?"

def test_pressing_twice_stops_only_once(app):
    calls = []
    w, inserted = _listening_window(app, calls)
    w._edit.setPlainText("Text")
    w._do_insert()
    w._do_insert()
    assert calls == [True]
    w._apply_state("idle")
    w._on_listening_done()
    assert inserted == ["Text"]
    w.close()

def test_without_a_running_microphone_insert_is_immediate(app):
    calls = []
    w, inserted = _listening_window(app, calls)
    w._apply_state("idle")
    w._edit.setPlainText("Sofort")
    w._do_insert()
    assert calls == [] and inserted == ["Sofort"]
    w.close()


# -- insert / close while the microphone still runs ---------------------------

def _listening_window(app, finish_calls):
    import dictation_window as dw
    inserted = []
    w = dw.DictationWindow(on_insert=lambda text: inserted.append(text) or True,
                           on_finish_listening=finish_calls.append)
    w._apply_state("recording")
    return w, inserted


def test_insert_waits_for_the_last_part(app):
    calls = []
    w, inserted = _listening_window(app, calls)
    w._edit.setPlainText("Erster Satz.")
    _cursor_to_end(w)
    w._do_insert()
    assert calls == [True] and inserted == [], "the microphone is stopped first"
    # the last part arrives, then the module reports the microphone off
    w._on_transcript_checked("Zweiter Satz.", "auto", [])
    w._apply_state("idle")
    w._on_listening_done()
    assert inserted == ["Erster Satz. Zweiter Satz."]
    w.close()


def test_close_drops_what_the_microphone_still_delivers(app):
    calls = []
    w, inserted = _listening_window(app, calls)
    w._edit.setPlainText("Weg damit")
    w._close_and_clear()
    assert calls == [False]
    w._on_transcript_checked("Nachzügler", "auto", [])
    assert w.text() == ""
    w._apply_state("idle")
    w._apply_state("recording")             # the next recording writes again
    w._on_transcript_checked("Neu.", "auto", [])
    assert w.text() == "Neu."
    w.close()


def test_the_module_finishes_the_recording_then_reports(app, monkeypatch):
    import threading

    import module as dic
    m = dic.DictationModule()
    order = []

    class _Win:
        def listening_done(self):
            order.append("done")
    m._window = _Win()
    m._state = "recording"
    monkeypatch.setattr(m, "_stop_and_transcribe",
                        lambda: (order.append("stopped"),
                                 setattr(m, "_state", "idle")))
    monkeypatch.setattr(threading, "Thread",
                        lambda target, **_k: type(
                            "T", (), {"start": lambda self: target()})())
    m.finish_listening(True)
    assert order == ["stopped", "done"]

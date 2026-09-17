"""Live dictation test variant: text appears while speaking.

The core (streaming.py) is tested without any model: a fake recogniser
returns what a real one would say after so many seconds of audio.
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


# -- settling words ------------------------------------------------------------

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


# -- the session -----------------------------------------------------------------

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
    _run(s, _tone(9.0))
    assert len(finals) >= 2


# -- the window ------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    from withease.core.event_bus import bus
    import dict_i18n
    import dictation_window as dw
    before = dict_i18n._lang.code
    bus.publish("i18n.language_changed", lang="de")
    w = dw.DictationWindow(on_insert=lambda _t: None, on_copy=lambda _t: None)
    yield w
    w.close()
    bus.publish("i18n.language_changed", lang=before)


def _grey_chars(win):
    from PySide6.QtGui import QTextCursor
    doc = win._edit.document()
    grey = []
    for i in range(len(win.text())):
        cur = QTextCursor(doc)
        cur.setPosition(i + 1)
        if cur.charFormat().foreground().color().red() == 150:
            grey.append(win.text()[i])
    return "".join(grey)


def _cursor_to_end(win):
    cur = win._edit.textCursor()
    cur.movePosition(cur.MoveOperation.End)
    win._edit.setTextCursor(cur)


def test_settled_black_tail_grey(app, win):
    win._apply_stream("Kannst du", "mir bei")
    assert win.text() == "Kannst du mir bei"
    assert _grey_chars(win) == "mir bei"

    win._apply_stream("Kannst du mir bei", "Gelegenheit")
    assert win.text() == "Kannst du mir bei Gelegenheit"
    assert _grey_chars(win) == "Gelegenheit"


def test_live_text_continues_existing_text(app, win):
    win._edit.setPlainText("Hallo Alex,")
    _cursor_to_end(win)
    win._apply_stream("Das", "ist")
    assert win.text() == "Hallo Alex, das ist"


def test_the_finished_sentence_replaces_the_live_text(app, win):
    win._edit.setPlainText("Erster Satz.")
    _cursor_to_end(win)
    win._apply_stream("Kannst du mir", "bei")
    win._apply_stream_final("Kannst du mir bei Gelegenheit helfen?", "auto")
    assert win.text() == "Erster Satz. Kannst du mir bei Gelegenheit helfen?"
    assert _grey_chars(win) == ""


def test_a_voice_command_still_works_at_the_end(app, win):
    win._edit.setPlainText("Hallo")
    _cursor_to_end(win)
    win._apply_stream("Neue", "Zeile")
    win._apply_stream_final("Neue Zeile", "auto")
    assert win.text() == "Hallo\n"


def test_nothing_recognised_removes_the_live_text(app, win):
    win._edit.setPlainText("Hallo")
    _cursor_to_end(win)
    win._apply_stream("", "hm")
    win._apply_stream_final("", "auto")
    assert win.text() == "Hallo"


def test_editing_meanwhile_is_left_alone(app, win):
    win._apply_stream("Kannst", "du")
    win._edit.setPlainText("Ganz anderer Text")
    _cursor_to_end(win)
    win._apply_stream("Das ist", "gut")
    assert win.text() == "Ganz anderer Text das ist gut"


# -- the module --------------------------------------------------------------------

@pytest.fixture
def module(app):
    import module as dic
    m = dic.DictationModule()
    m._settings.update({"backend": "local", "output_mode": "window"})
    return m


def test_only_switched_on_it_takes_over_the_key(module):
    assert not module._stream_wanted()
    module._settings["stream_enabled"] = True
    assert module._stream_wanted()
    module._settings["backend"] = "cloud"
    assert not module._stream_wanted()


def test_the_finished_sentence_gets_the_normal_treatment(module, monkeypatch):
    got = []

    class _Win:
        def stream_final(self, text, mode="auto"):
            got.append((text, mode))
    module._window = _Win()
    module._active_mode = "auto"
    monkeypatch.setattr(module, "_refine_transcript",
                        lambda text: text + " [fertig]")
    module._on_stream_final("Hallo Welt")
    module._on_stream_final("")
    assert got == [("Hallo Welt [fertig]", "auto"), ("", "auto")]


def test_parakeet_without_its_environment_says_so(module, monkeypatch):
    import parakeet
    monkeypatch.setattr(parakeet, "available", lambda: False)
    module._settings.update({"stream_enabled": True,
                             "stream_engine": "parakeet"})
    errors = []
    monkeypatch.setattr(module, "_error",
                        lambda msg, fixable=False: errors.append(msg))
    monkeypatch.setattr(module, "_capture_target", lambda: None)
    module.start_stream()
    assert errors and module._live_session is None
    assert not module._live_starting


def test_the_settings_show_the_live_rows_only_when_switched_on(app, module):
    page = module.get_settings_widget()
    assert page._stream_cb.isVisibleTo(page)
    assert not page._stream_engine.isVisibleTo(page)
    page._stream_cb.setChecked(True)
    assert page._stream_engine.isVisibleTo(page)
    assert module._settings["stream_enabled"] is True
    page.deleteLater()


# -- the Parakeet client -------------------------------------------------------------

def test_the_parakeet_client_speaks_the_worker_protocol(tmp_path, monkeypatch):
    """A stand-in worker answers like the real one; no model needed."""
    import parakeet
    fake = tmp_path / "fake_worker.py"
    fake.write_text(
        "import json, sys\n"
        "print(json.dumps({'ready': True, 'device': 'cpu'}), flush=True)\n"
        "for line in sys.stdin:\n"
        "    r = json.loads(line)\n"
        "    print(json.dumps({'id': r['id'], 'text': 'Hallo ' + str(len(r['pcm']))}),"
        " flush=True)\n", encoding="utf-8")
    monkeypatch.setattr(parakeet, "python_in", lambda _f: sys.executable)
    engine = parakeet.ParakeetEngine(str(tmp_path))
    real_popen = parakeet.subprocess.Popen

    def popen(args, **kw):
        return real_popen([sys.executable, str(fake)], **kw)
    monkeypatch.setattr(parakeet.subprocess, "Popen", popen)
    try:
        engine.start()
        assert engine.device == "cpu"
        assert engine.transcribe(b"\x00\x00" * 3).startswith("Hallo ")
    finally:
        engine.stop()


def test_the_parakeet_folder_is_found_through_the_pointer_file(tmp_path,
                                                               monkeypatch):
    import parakeet
    folder = tmp_path / "anywhere"
    (folder / "venv" / "Scripts").mkdir(parents=True)
    (folder / "venv" / "Scripts" / "python.exe").write_bytes(b"")
    home = tmp_path / "appdata"
    (home / "WithEase").mkdir(parents=True)
    (home / "WithEase" / "parakeet-test.txt").write_text(str(folder),
                                                         encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(home))
    monkeypatch.delenv("WITHEASE_PARAKEET_DIR", raising=False)
    assert parakeet.env_dir() == str(folder)


# -- telling speech from noise -------------------------------------------------------

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


# -- only your own voice ---------------------------------------------------------------

@pytest.mark.parametrize("spoken, want", [
    ("Hallo, wie geht das hier? Mm-hmm.", "Hallo, wie geht das hier?"),
    ("Mhm, ja genau.", "Ja genau."),
    ("Der Himmel ist blau, ähm, glaube ich.", "Der Himmel ist blau, glaube ich."),
    ("Hmm", ""),
    ("Umzug nach Hamburg.", "Umzug nach Hamburg."),
])
def test_filler_sounds_are_removed(spoken, want):
    assert st.clean_fillers(spoken) == want


@pytest.mark.parametrize("text, foreign", [
    ("I think we should go there now", True),
    ("Yes, I know what you mean.", True),
    ("Das ist okay für mich", False),
    ("Das Meeting mit the team ist heute", False),
    ("Okay", False),
])
def test_an_english_sentence_is_recognised_as_foreign(text, foreign):
    assert st.looks_foreign(text, "de") is foreign
    assert st.looks_foreign(text, "en") is False


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

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


@pytest.fixture(autouse=True)
def _no_real_canary(monkeypatch):
    """Canary may really be installed on the machine running the tests;
    starting it would take a minute and several gigabytes of memory."""
    import parakeet
    monkeypatch.setattr(parakeet, "canary_available", lambda: False)


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
    _run(s, _tone(13.0))
    assert len(finals) >= 2
    assert s.last_finish["end"] == "long"


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
        def stream_final(self, text, mode="auto", marks="auto"):
            got.append((text, mode))
    module._window = _Win()
    module._active_mode = "auto"
    monkeypatch.setattr(module, "_refine_transcript",
                        lambda text, **_kw: text + " [fertig]")
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


# -- thinking pauses and full stops ------------------------------------------------------

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


@pytest.mark.parametrize("text, want", [
    ("Ich möchte besser Pausen. Und dann weiter.",
     "Ich möchte besser Pausen und dann weiter."),
    ("Ich denke. Dass es geht.", "Ich denke, dass es geht."),
    ("Das war gut. Wenn du kommst, sag Bescheid.",
     "Das war gut. Wenn du kommst, sag Bescheid."),
    ("Das war gut. Aber nicht ganz.", "Das war gut. Aber nicht ganz."),
])
def test_a_stop_before_a_continuation_is_taken_back(text, want):
    assert st.merge_continuations(text) == want


@pytest.mark.parametrize("text, want", [
    ("Wir treffen uns. Und reden?", "Wir treffen uns und reden"),
    ("Das kostet 3.5 Euro, z.B. heute usw.", "Das kostet 3.5 Euro, z.B. heute usw."),
])
def test_only_the_recognisers_own_marks_are_removed(text, want):
    assert st.strip_sentence_marks(text) == want


@pytest.mark.parametrize("spoken, want", [
    ("das ist gut Punkt neuer Gedanke", "das ist gut. Neuer Gedanke"),
    ("hallo neuer Satz wie geht es Ausrufezeichen", "hallo. Wie geht es!"),
    ("der Punkt ist wichtig Komma sagt er Fragezeichen",
     "der Punkt ist wichtig, sagt er?"),
    ("ein Komma fehlt hier Punkt", "ein Komma fehlt hier."),
    ("Punkt", "Punkt"),
    ("Punkt neuer Gedanke", ". Neuer Gedanke"),
])
def test_spoken_marks(spoken, want):
    assert st.apply_spoken_marks(spoken) == want


def test_the_window_takes_back_a_stop_when_the_sentence_goes_on(app, win):
    win._apply_stream("Ich möchte besser", "Pausen")
    win._apply_stream_final("Ich möchte besser Pausen.", "auto", "auto")
    assert win.text() == "Ich möchte besser Pausen."
    win._apply_stream("Und", "dann")
    win._apply_stream_final("Und dann weiter.", "auto", "auto")
    assert win.text() == "Ich möchte besser Pausen und dann weiter."


def test_a_subordinate_clause_gets_its_comma(app, win):
    win._apply_stream_final("Ich glaube.", "auto", "auto")
    win._apply_stream_final("Dass es klappt.", "auto", "auto")
    assert win.text() == "Ich glaube, dass es klappt."


def test_a_real_new_sentence_keeps_its_stop(app, win):
    win._apply_stream_final("Das war gut.", "auto", "auto")
    win._apply_stream_final("Morgen geht es weiter.", "auto", "auto")
    assert win.text() == "Das war gut. Morgen geht es weiter."


def test_spoken_marks_are_never_taken_back(app, win):
    win._apply_stream_final("Das war gut.", "auto", "spoken")
    win._apply_stream_final("Und dann weiter", "auto", "spoken")
    assert win.text() == "Das war gut. Und dann weiter"


def test_a_spoken_stop_after_a_pause_attaches_to_the_text(app, win):
    win._apply_stream_final("Das war gut", "auto", "spoken")
    win._apply_stream_final(". Neuer Gedanke", "auto", "spoken")
    assert win.text() == "Das war gut. Neuer Gedanke"


def test_the_final_sentence_in_spoken_mode(module, monkeypatch):
    got = []

    class _Win:
        def stream_final(self, text, mode="auto", marks="auto"):
            got.append((text, marks))
    module._window = _Win()
    module._active_mode = "auto"
    module._settings["stream_punct"] = "spoken"
    module._on_stream_final("Das ist gut. Punkt. Neuer Gedanke")
    text, marks = got[-1]
    assert marks == "spoken"
    assert text.startswith("Das ist gut. Neuer Gedanke")


def test_the_final_sentence_in_automatic_mode(module):
    got = []

    class _Win:
        def stream_final(self, text, mode="auto", marks="auto"):
            got.append((text, marks))
    module._window = _Win()
    module._active_mode = "auto"
    module._on_stream_final("Ich möchte besser Pausen. Und dann weiter.")
    assert got[-1] == ("Ich möchte besser Pausen und dann weiter.", "auto")


def test_the_new_live_settings_appear_with_the_switch(app, module):
    page = module.get_settings_widget()
    page._stream_cb.setChecked(True)
    assert page._stream_punct.isVisibleTo(page)
    assert page._stream_pause.value() == 2.5
    page._stream_punct.setCurrentIndex(page._stream_punct.findData("spoken"))
    assert module._settings["stream_punct"] == "spoken"
    page.deleteLater()


# -- short words, numbers, trailing clauses ---------------------------------------

def test_a_short_loud_word_is_not_taken_for_a_background_voice():
    """"ist" is mostly a soft vowel and a hiss; its loud part decides."""
    rec, updates, finals = _Recogniser(), [], []
    s = _session(rec, updates, finals, background_ratio=0.25, voice_level=6000)
    word = _tone(0.1, amplitude=7000) + _tone(0.4, amplitude=900)
    _run(s, _silence(0.3) + word + _silence(1.2), chunk_s=0.05)
    assert s.last_finish["result"] != "quieter than your voice"


@pytest.mark.parametrize("text, want", [
    ("Plus plus siebenvierzig.", "++47."),
    ("Ich programmiere in C plus plus.", "Ich programmiere in C++."),
    ("Das macht fünf plus drei.", "Das macht 5 + 3."),
    ("Wir waren zwei Wochen weg.", "Wir waren zwei Wochen weg."),
    ("Das sind sieben Prozent.", "Das sind 7 %."),
    ("Im Jahr zweitausend sechsundzwanzig.", "Im Jahr 2026."),
    ("Er ist dreihundertvierzig Meter gelaufen.", "Er ist 340 Meter gelaufen."),
    ("Ein Mann und eine Frau.", "Ein Mann und eine Frau."),
    ("Hundert Dank.", "Hundert Dank."),
    ("Ein Plus für dich.", "Ein Plus für dich."),
])
def test_spoken_numbers_and_signs(text, want):
    assert st.spoken_numbers(text) == want


@pytest.mark.parametrize("text, want", [
    ("Ich sage dir dann Bescheid. Wenn es wieder vorkommt.",
     "Ich sage dir dann Bescheid, wenn es wieder vorkommt."),
    ("Das war gut. Wenn es regnet, bleibe ich zu Hause.",
     "Das war gut. Wenn es regnet, bleibe ich zu Hause."),
    ("Ich weiß nicht. Ob das geht.", "Ich weiß nicht, ob das geht."),
])
def test_a_trailing_clause_joins_the_sentence_before(text, want):
    assert st.merge_continuations(text) == want


def test_the_window_joins_a_trailing_clause_after_a_pause(app, win):
    win._apply_stream_final("Ich sage dir dann Bescheid.", "auto", "auto")
    win._apply_stream_final("Wenn es wieder vorkommt.", "auto", "auto")
    assert win.text() == "Ich sage dir dann Bescheid, wenn es wieder vorkommt."


@pytest.mark.parametrize("text, want", [
    ("Yeah.", "Ja."), ("Had", "Hat"), ("Nine", "Nein"),
    ("Yeah that is", "Yeah that is"), ("Obama", "Obama"),
])
def test_a_single_english_look_alike_becomes_german(text, want):
    assert st.fix_short_english(text) == want


def test_short_parakeet_words_are_fixed(module):
    class _Parakeet:
        def transcribe(self, pcm, final=False):
            return "Yeah."
    module._parakeet = _Parakeet()
    assert module._stream_parakeet(b"\x00\x00" * 8000, True) == "Ja."


def test_the_misheard_quotation_word_works():
    import commands_de as cde
    got = cde.apply_inline_punctuation(
        "Er sagte Anführerstriche unten Hallo Anführerstriche oben und ging.")
    assert got == "Er sagte „Hallo“ und ging."


@pytest.mark.parametrize("text, want", [
    ("Es kostet siebenundvierzig Euro.", "Es kostet 47 €."),
    ("Es kostet fünf Euro fünfzig.", "Es kostet 5,50 €."),
    ("Der Euro ist stark.", "Der Euro ist stark."),
    ("Draußen sind minus fünf Grad.", "Draußen sind -5°."),
    ("Heute sind zwanzig Grad Celsius.", "Heute sind 20 °C."),
    ("Das Spiel ging drei minus zwei aus.", "Das Spiel ging 3 - 2 aus."),
    ("Siehe Paragraph fünf.", "Siehe § 5."),
    ("Nach Paragraf dreizehn ist das erlaubt.", "Nach § 13 ist das erlaubt."),
    ("Die Paragraphen drei und 4 gelten.", "Die §§ 3 und 4 gelten."),
    ("Dieser Paragraph ist wichtig.", "Dieser Paragraph ist wichtig."),
])
def test_euro_degrees_minus_and_paragraph_signs(text, want):
    assert st.spoken_numbers(text) == want


# -- the dictionary without hotwords ----------------------------------------------------

_WORDS = ["MediaMarkt", "WithEase", "Rechnung", "Deutsche Rentenversicherung",
          "Parakeet", "OpenWhispr", "Schreiben", "Beginn", "Grüßen"]


@pytest.mark.parametrize("text, want", [
    ("Bei Media Markt gekauft.", "Bei MediaMarkt gekauft."),
    ("Bei Mediamark gekauft.", "Bei MediaMarkt gekauft."),
    ("Ich nutze withease täglich.", "Ich nutze WithEase täglich."),
    ("Die Deutsche Renten Versicherung schreibt.",
     "Die Deutsche Rentenversicherung schreibt."),
    ("Parakit ist schnell.", "Parakeet ist schnell."),
    ("Open Whisper ist ein Programm.", "OpenWhispr ist ein Programm."),
])
def test_dictionary_words_come_back(text, want):
    assert st.match_dictionary(text, _WORDS) == want


@pytest.mark.parametrize("text", [
    "Die Rechnungen liegen hier.",          # an ending, not a misspelling
    "Ich muss noch schreiben.",             # verb, not the noun
    "Das beginnt gleich.",                  # lower case: an ordinary word
    "Mit großen Schritten.",
    "Weißt du, wie das geht?",
    "Die Medien machen Markt.",
    "Nach Namen sortiert.",
])
def test_ordinary_german_stays_as_it_is(text):
    assert st.match_dictionary(text, _WORDS + ["Nachnamen"]) == text


def test_cologne_phonetics():
    assert st.cologne_code("Müller-Lüdenscheidt") == "65752682"
    assert st.cologne_code("Wikipedia") == "3412"
    assert st.cologne_code("Parakit") == st.cologne_code("Parakeet")


def test_the_final_sentence_uses_the_dictionary_and_every_correction(module):
    got = []

    class _Win:
        def stream_final(self, text, mode="auto", marks="auto"):
            got.append(text)
    module._window = _Win()
    module._active_mode = "auto"
    module._settings["dictionary"] = [{"w": "MediaMarkt", "s": "", "src": "user"}]

    class _Memory:
        def apply_all(self, text):
            return text.replace("Rechnug", "Rechnung")

        def apply(self, text, uncertain=None):
            raise AssertionError("the live test has no uncertainty to go by")
    module._error_memory = _Memory()
    module._on_stream_final("Die Rechnug von Media Markt")
    assert "Rechnung" in got[-1] and "MediaMarkt" in got[-1]


def test_the_live_microphone_feeds_the_level_bar(module):
    from withease.core.event_bus import bus
    levels = []

    def listen(level, **_):
        levels.append(level)
    bus.subscribe("dictation.level", listen)
    try:
        module._level_sent = 0.0
        module._publish_live_level(b"\x00\x40" * 800)
    finally:
        bus.unsubscribe("dictation.level", listen)
    assert levels and levels[-1] > 0.4


# -- marked words, lost sentences, loading -------------------------------------------------

def _select(win, start, end):
    cur = win._edit.textCursor()
    cur.setPosition(start)
    cur.setPosition(end, cur.MoveMode.KeepAnchor)
    win._edit.setTextCursor(cur)


def test_a_marked_word_is_replaced_by_the_live_sentence(app, win):
    win._edit.setPlainText("Ich habe den Nachricht heute gelesen.")
    _select(win, 9, 22)                                   # "den Nachricht"
    win._apply_stream("die", "Nachricht")
    assert win.text() == "Ich habe die Nachricht heute gelesen."
    win._apply_stream_final("Die Nachricht.", "auto", "auto")
    assert win.text() == "Ich habe die Nachricht heute gelesen."


def test_a_marked_word_stays_when_nothing_was_recognised(app, win):
    win._edit.setPlainText("Ich habe den Brief gelesen.")
    _select(win, 13, 18)                                  # "Brief"
    win._apply_stream("", "hm")
    win._apply_stream_final("", "auto", "auto")
    assert win.text() == "Ich habe den Brief gelesen."


def test_the_command_key_does_not_swallow_a_sentence_that_was_shown(app, win):
    win._edit.setPlainText("Hallo")
    _cursor_to_end(win)
    win._apply_stream("das ist", "gut")
    win._apply_stream_final("Das ist gut.", "command", "auto")
    assert win.text() == "Hallo das ist gut."


def test_while_the_correction_window_waits_nothing_is_written(app, win):
    class _Dialog:
        def __init__(self):
            self.heard = []

        def isVisible(self):
            return True

        def handle_voice(self, text):
            self.heard.append(text)
    dialog = _Dialog()
    win._correction_dialog = dialog
    win._edit.setPlainText("Hallo")
    win._apply_stream("Brief", "")
    assert win.text() == "Hallo"
    win._apply_stream_final("Brief", "auto", "auto")
    assert dialog.heard == ["Brief"] and win.text() == "Hallo"
    win._correction_dialog = None


def test_the_chip_says_loading_not_recognising(module, monkeypatch):
    states = []
    monkeypatch.setattr(module, "_set_state",
                        lambda state, detail="": states.append((state, detail)))
    monkeypatch.setattr(module, "_capture_target", lambda: None)
    module._settings.update({"stream_enabled": True,
                             "stream_engine": "parakeet"})

    class _Slow:
        def ready(self):
            return False

        def start(self):
            raise RuntimeError("stop here")
    module._parakeet = _Slow()
    monkeypatch.setattr(module, "_error", lambda *a, **k: None)
    module.start_stream()
    assert states and states[0][0] == "loading"
    assert "Parakeet" in states[0][1]


def test_the_live_recogniser_is_preloaded_at_start(module, monkeypatch):
    started = []

    class _Engine:
        def start(self):
            started.append(True)
    module._settings.update({"stream_enabled": True,
                             "stream_engine": "parakeet"})
    import parakeet
    monkeypatch.setattr(parakeet, "available", lambda: True)
    module._parakeet = _Engine()
    import threading
    monkeypatch.setattr(threading, "Thread",
                        lambda target, **_k: type(
                            "T", (), {"start": lambda self: target()})())
    module.preload_stream_engine()
    assert started == [True]


def test_parakeet_starts_only_once(tmp_path, monkeypatch):
    import threading
    import parakeet
    engine = parakeet.ParakeetEngine(str(tmp_path))
    calls = []

    def slow_start():
        calls.append(1)
        engine._ready = True
        engine._proc = type("P", (), {"poll": lambda self: None})()
    monkeypatch.setattr(engine, "_start", slow_start)
    threads = [threading.Thread(target=engine.start) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == [1]



def test_canary_writes_the_finished_sentence_in_the_set_language(module,
                                                                 monkeypatch):
    calls = []

    class _Engine:
        def __init__(self, text):
            self.text = text

        def ready(self):
            return True

        def transcribe(self, pcm, final=False, language=""):
            calls.append((self.text, final, language))
            return self.text
    module._parakeet = _Engine("Had")
    module._canary = _Engine("Hat")
    monkeypatch.setattr(module, "_canary_wanted", lambda: True)
    assert module._stream_parakeet(b"\x00\x00" * 800, True) == "Hat"
    assert module._stream_parakeet(b"\x00\x00" * 800, False) == "Hat",         "grey words from Parakeet, with its look-alike fixed"
    assert calls[0] == ("Hat", True, "de")
    assert calls[1][0] == "Had"


def test_without_canary_parakeet_finishes_the_sentence(module, monkeypatch):
    class _Engine:
        def transcribe(self, pcm, final=False, language=""):
            return "Nine"
    module._parakeet = _Engine()
    monkeypatch.setattr(module, "_canary_wanted", lambda: False)
    assert module._stream_parakeet(b"\x00\x00" * 800, True) == "Nein"


def test_a_failing_canary_falls_back_to_parakeet(module, monkeypatch):
    class _Broken:
        def ready(self):
            return True

        def transcribe(self, *a, **k):
            raise RuntimeError("out of memory")

    class _Parakeet:
        def transcribe(self, pcm, final=False, language=""):
            return "Hallo"
    module._canary, module._parakeet = _Broken(), _Parakeet()
    monkeypatch.setattr(module, "_canary_wanted", lambda: True)
    assert module._stream_parakeet(b"\x00\x00" * 800, True) == "Hallo"



@pytest.mark.parametrize("text, want", [
    ("Plus plus sieben und vierzig.", "++47."),
    ("Es kostet sieben und vierzig Euro.", "Es kostet 47 €."),
    ("Ich habe zwei und drei Äpfel.", "Ich habe zwei und drei Äpfel."),
])
def test_a_number_written_apart_is_one_number(text, want):
    assert st.spoken_numbers(text) == want


def test_a_lone_verb_is_no_question():
    from postprocess import fix_question_marks
    assert fix_question_marks("Ist.") == "Ist."
    assert fix_question_marks("Hast du Zeit.") == "Hast du Zeit?"



# -- a single word heard after the previous sentence ------------------------------------

_CTX = ("Kannst du mir bei Gelegenheit die Rechnung raussuchen, die wir "
        "letzte Woche bei Mediamarkt bekommen haben?")


@pytest.mark.parametrize("full, want", [
    (_CTX + " Ja.", "Ja."),
    (_CTX[:-7] + " habens. Obama.", "Obama."),
    (_CTX[:-1] + " S. Obama.", "Obama."),
    (_CTX + " Plus plus 47.", "Plus plus 47."),
    ("Etwas ganz anderes hier.", None),
    (_CTX, None),
])
def test_the_context_sentence_is_cut_off_again(full, want):
    assert st.strip_context(full, _CTX) == want


class _LanguageGuesser:
    """Like Parakeet: alone a short word comes out English, after a German
    sentence it comes out German."""

    def __init__(self):
        self.calls = 0

    def __call__(self, pcm, final):
        self.calls += 1
        seconds = len(pcm) / (st.RATE * 2)
        if seconds > 2.5:
            return "Das ist ein Satz. Ja." if seconds > 3.2 else "Das ist ein Satz."
        return "Yeah." if seconds < 1.5 else "Das ist ein Satz."


def test_a_short_word_gets_the_previous_sentence_as_context():
    finals = []
    rec = _LanguageGuesser()
    s = st.StreamSession(rec, lambda *_: None, finals.append, short_s=1.5,
                         context_s=2.0)
    _run(s, _silence(0.3) + _tone(2.0) + _silence(1.2))     # a sentence
    _run(s, _silence(0.3) + _tone(0.4) + _silence(1.2))     # then "ja"
    assert finals[-1] == "Ja."
    assert s.last_finish.get("context") is True


def test_without_context_the_word_stands_alone():
    finals = []
    s = st.StreamSession(_LanguageGuesser(), lambda *_: None, finals.append,
                         short_s=0.0)
    _run(s, _silence(0.3) + _tone(2.0) + _silence(1.2))
    _run(s, _silence(0.3) + _tone(0.4) + _silence(1.2))
    assert finals[-1] == "Yeah."


def test_the_first_word_of_a_session_has_no_context_to_use():
    finals = []
    s = st.StreamSession(_LanguageGuesser(), lambda *_: None, finals.append,
                         short_s=1.5)
    _run(s, _silence(0.3) + _tone(0.4) + _silence(1.2))
    assert finals[-1] == "Yeah."
    assert "context" not in s.last_finish


# -- insert / close while the microphone still runs ----------------------------------------

def _listening_window(app, finish_calls):
    import dictation_window as dw
    inserted = []
    w = dw.DictationWindow(on_insert=lambda text: inserted.append(text) or True,
                           on_finish_listening=finish_calls.append)
    w._apply_state("recording", "Live")
    return w, inserted


def test_insert_waits_for_the_last_sentence(app):
    calls = []
    w, inserted = _listening_window(app, calls)
    w._edit.setPlainText("Erster Satz.")
    _cursor_to_end(w)
    w._do_insert()
    assert calls == [True] and inserted == [], "the microphone is stopped first"
    # the last sentence arrives, then the module reports the microphone off
    w._apply_stream_final("Zweiter Satz.", "auto", "auto")
    w._apply_state("idle")
    w._on_listening_done()
    assert inserted == ["Erster Satz. Zweiter Satz."]
    w.close()


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


def test_close_drops_what_the_microphone_still_delivers(app):
    calls = []
    w, inserted = _listening_window(app, calls)
    w._edit.setPlainText("Weg damit")
    w._close_and_clear()
    assert calls == [False]
    w._apply_stream("Nachzügler", "")
    w._apply_stream_final("Nachzügler.", "auto", "auto")
    w._on_transcript_checked("Nachzügler", "auto", [])
    assert w.text() == ""
    w._apply_state("idle")
    w._apply_state("recording", "Live")          # the next session writes again
    w._apply_stream_final("Neu.", "auto", "auto")
    assert w.text() == "Neu."
    w.close()


def test_without_a_running_microphone_insert_is_immediate(app):
    calls = []
    w, inserted = _listening_window(app, calls)
    w._apply_state("idle")
    w._edit.setPlainText("Sofort")
    w._do_insert()
    assert calls == [] and inserted == ["Sofort"]
    w.close()


def test_the_module_finishes_a_live_session_then_reports(module, monkeypatch):
    import threading
    order = []

    class _Win:
        def listening_done(self):
            order.append("done")
    module._window = _Win()
    module._live_session = object()
    monkeypatch.setattr(module, "stop_stream",
                        lambda: (order.append("stopped"),
                                 setattr(module, "_live_session", None)))
    monkeypatch.setattr(threading, "Thread",
                        lambda target, **_k: type(
                            "T", (), {"start": lambda self: target()})())
    module.finish_listening(True)
    assert order == ["stopped", "done"]

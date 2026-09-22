"""Normal dictation with the microphone left on: every longer pause turns
what was said so far into text, the recording goes on.

No model and no microphone: a fake recogniser and generated audio."""
import math
import os
import struct
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

import streaming as st  # noqa: E402


def _tone(seconds, amplitude=6000):
    n = int(seconds * st.RATE)
    return b"".join(struct.pack("<h", int(amplitude * math.sin(i / 5)))
                    for i in range(n))


def _silence(seconds, level=40):
    n = int(seconds * st.RATE)
    return b"".join(struct.pack("<h", level if i % 2 else -level)
                    for i in range(n))


def _feed(session, audio, block=1600):
    for i in range(0, len(audio), block):
        session.put(audio[i:i + block])


class _Win:
    def __init__(self):
        self.got = []

    def handle_transcript(self, text, mode="auto", low=None):
        self.got.append((text, mode))

    def set_state(self, *_a):
        pass


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def module(app, monkeypatch):
    import module as dic
    m = dic.DictationModule()
    m._settings.update({"backend": "local", "output_mode": "window"})
    m._window = _Win()
    m._active_mode = "auto"
    # the speech detector without Silero: generated tones are not speech
    monkeypatch.setattr(st, "make_gate", lambda *_a, **_k: st.EnergyGate())
    heard = []

    def transcribe(wav):
        heard.append(len(wav))
        return f"Teil {len(heard)}."
    monkeypatch.setattr(m, "transcribe", transcribe)
    monkeypatch.setattr(m, "_refine_transcript", lambda text, **_k: text)
    m.heard = heard
    return m


def test_on_by_default_for_the_window_only(module):
    assert module._segment_wanted()
    module._active_mode = "command"
    assert not module._segment_wanted()          # commands stay one piece
    module._active_mode = "auto"
    module._settings["output_mode"] = "direct"
    assert not module._segment_wanted()
    module._settings["output_mode"] = "window"
    module._settings["segment_on_pause"] = False
    assert not module._segment_wanted()


def test_a_pause_converts_while_the_microphone_stays_on(module):
    module._settings["segment_pause"] = 1.0
    session = module._make_segmenter()
    session.start()
    _feed(session, _silence(0.3) + _tone(1.0) + _silence(1.3))
    _feed(session, _tone(0.8) + _silence(0.2))
    import time
    deadline = time.monotonic() + 5
    while not module._window.got and time.monotonic() < deadline:
        time.sleep(0.02)
    # the first part is text already - the second one is still being spoken
    assert module._window.got == [("Teil 1.", "auto")]
    module._segmenter = session
    module._state = "recording"
    module._stop_segmented()
    assert module._window.got == [("Teil 1.", "auto"), ("Teil 2.", "auto")]
    assert module._state == "idle"


def test_a_thinking_pause_stays_in_the_same_part(module):
    module._settings["segment_pause"] = 1.5
    session = module._make_segmenter()
    session.start()
    _feed(session, _tone(0.8) + _silence(0.8) + _tone(0.8) + _silence(0.2))
    module._segmenter = session
    module._state = "recording"
    module._stop_segmented()
    assert module._window.got == [("Teil 1.", "auto")]
    assert len(module.heard) == 1


def test_throwing_the_recording_away_delivers_nothing_more(module):
    session = module._make_segmenter()
    session.start()
    _feed(session, _tone(1.0))
    module._segmenter = session
    module._state = "recording"
    module._abort_recording()
    session.stop(timeout=5)
    assert module._window.got == []
    assert module._segmenter is None and module._state == "idle"


def test_nothing_said_at_all_is_reported(module, monkeypatch):
    said = []
    monkeypatch.setattr(module, "_say_nothing_heard", said.append)
    session = module._make_segmenter()
    session.start()
    _feed(session, _silence(1.0))
    module._segmenter = session
    module._state = "recording"
    module._stop_segmented()
    assert said == ["empty"] and module.heard == []


def test_the_settings_show_the_pause_rows_with_the_switch(app, module):
    page = module.get_settings_widget()
    assert page._segment_cb.isVisibleTo(page)
    assert page._segment_pause.isVisibleTo(page)
    assert page._pause_dot_cb.isVisibleTo(page)
    page._segment_cb.setChecked(False)
    assert not page._segment_pause.isVisibleTo(page)
    assert not page._pause_dot_cb.isVisibleTo(page)
    assert module._settings["segment_on_pause"] is False
    page.deleteLater()


# -- the countdown while you pause ---------------------------------------------

def test_a_pause_counts_down_until_the_part_is_converted(module):
    shown = []
    session = st.StreamSession(lambda pcm, final: "x", lambda *_: None,
                               lambda _t: None, step_s=1e9, pause_s=1.0,
                               detector=st.EnergyGate(),
                               on_silence=shown.append)
    session.start()
    import time
    _feed(session, _tone(0.8))
    time.sleep(0.2)
    for _ in range(12):                  # a second and more of quiet, live
        _feed(session, _silence(0.1))
        time.sleep(0.03)
    session.stop(timeout=5)
    numbers = [s for s in shown if s is not None]
    assert numbers and numbers[0] <= 0.7          # only after a short gap
    assert numbers == sorted(numbers, reverse=True)
    assert shown[-1] is None                       # gone once converted


def test_speaking_again_takes_the_countdown_away(module):
    shown = []
    session = st.StreamSession(lambda pcm, final: "x", lambda *_: None,
                               lambda _t: None, step_s=1e9, pause_s=2.0,
                               detector=st.EnergyGate(),
                               on_silence=shown.append)
    session.start()
    import time
    _feed(session, _tone(0.8))
    time.sleep(0.2)
    _feed(session, _silence(0.6))
    time.sleep(0.2)
    _feed(session, _tone(0.3))
    time.sleep(0.2)
    assert any(s is not None for s in shown) and shown[-1] is None
    session.stop(timeout=5)


def test_the_chip_shows_the_seconds_left(app):
    import module as dic
    chip = dic.DictationIndicator()
    chip._apply_state("recording", "")
    assert chip._subtitle() == ""
    chip._apply_pause(1.4, 2.0)
    assert "1,4" in chip._subtitle() or "1.4" in chip._subtitle()
    width = chip.width()
    chip._apply_pause(0.3, 2.0)
    assert chip.width() == width                   # the line does not twitch
    chip._apply_pause(-1.0, 2.0)
    assert chip._subtitle() == ""
    chip._apply_pause(1.0, 2.0)
    chip._apply_state("transcribing", "")
    assert chip._subtitle() == ""
    chip.deleteLater()


def test_the_pause_goes_to_the_chip_and_the_window(module, monkeypatch):
    import module as dic
    sent, shown = [], []
    monkeypatch.setattr(dic.bus, "publish",
                        lambda topic, **kw: sent.append((topic, kw)))
    module._window.pause_countdown = lambda left, total: shown.append(left)
    module._publish_pause(1.5, 2.0)
    module._settings["pause_dot"] = False
    module._publish_pause(1.0, 2.0)
    module._publish_pause(None, 2.0)
    assert sent == [("dictation.pause", {"left": 1.5, "total": 2.0}),
                    ("dictation.pause", {"left": 1.0, "total": 2.0}),
                    ("dictation.pause", {"left": -1.0, "total": 2.0})]
    assert shown == [1.5, -1.0, -1.0]          # switched off: no dot


def test_the_dot_sits_right_after_the_text_cursor(app):
    import dictation_window as dw
    win = dw.DictationWindow(on_insert=lambda text: True, on_copy=lambda text: None)
    win._edit.setPlainText("Hallo Welt")
    cursor = win._edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    win._edit.setTextCursor(cursor)
    win._apply_state("recording")
    win._apply_pause(1.2, 2.0)
    dot = win._pause_dot
    assert dot.showing() and dot.isVisibleTo(win._edit.viewport())
    caret = win._edit.cursorRect()
    assert dot.x() > caret.right()
    assert dot.geometry().top() <= caret.center().y() <= dot.geometry().bottom()
    win._apply_pause(-1.0, 2.0)
    assert not dot.showing()
    win._apply_pause(1.0, 2.0)
    win._apply_state("transcribing")
    assert not dot.showing()
    win.deleteLater()


# -- a full stop that was only a thinking pause ----------------------------------

@pytest.mark.parametrize("previous, text, expected", [
    ("zwischen einem Satz lasse.", "Damit auch wirklich ein Punkt kommt.",
     "zwischen einem Satz lasse, damit auch wirklich ein Punkt kommt."),
    ("Ich komme morgen.", "Und bringe Kuchen mit.",
     "Ich komme morgen und bringe Kuchen mit."),
    ("Ruf mich an.", "Wenn es wieder vorkommt.",
     "Ruf mich an, wenn es wieder vorkommt."),
    # real sentence starts stay as they are
    ("Ich komme morgen.", "Wenn es regnet, bleibe ich zu Hause.",
     "Ich komme morgen. Wenn es regnet, bleibe ich zu Hause."),
    ("Ich komme morgen.", "Das Wetter ist gut.",
     "Ich komme morgen. Das Wetter ist gut."),
    ("Ich komme morgen.", "Aber erst spät.",
     "Ich komme morgen. Aber erst spät."),
    # a dot that is no sentence end
    ("Wir treffen uns am 16.", "Und dann sehen wir weiter.",
     "Wir treffen uns am 16. Und dann sehen wir weiter."),
    ("Das gilt z.B.", "Und so weiter.", "Das gilt z.B. Und so weiter."),
    ("Na gut...", "Und dann?", "Na gut... Und dann?"),
])
def test_a_pause_full_stop_is_taken_back(app, previous, text, expected):
    from PySide6.QtWidgets import QPlainTextEdit

    import editor_actions as ea
    edit = QPlainTextEdit()
    edit.setPlainText(previous)
    cursor = edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    edit.setTextCursor(cursor)
    ea.Editor(edit).insert_dictation(text)
    assert edit.toPlainText() == expected
    edit.deleteLater()


def test_two_parts_become_one_sentence_in_the_window(app):
    import dictation_window as dw
    win = dw.DictationWindow(on_insert=lambda text: True,
                             on_copy=lambda text: None)
    win._on_transcript("Da ist die Frage, ob ich lieber Pausen zwischen "
                       "einem Satz lasse.", "auto", [])
    win._on_transcript("Damit auch wirklich ein Punkt gesetzt wird.",
                       "auto", [])
    assert win.text() == ("Da ist die Frage, ob ich lieber Pausen zwischen "
                          "einem Satz lasse, damit auch wirklich ein Punkt "
                          "gesetzt wird.")
    win.deleteLater()


# -- spoken "Punkt" and "neue Zeile" inside a part ------------------------------

@pytest.mark.parametrize("spoken, expected", [
    ("Ich habe folgenden Fehler Punkt.", "Ich habe folgenden Fehler."),
    ("Ich habe folgenden Fehler Punkt. Neue Zeile.",
     "Ich habe folgenden Fehler.\n"),
    ("Fehler Punkt, neue Zeile. Wenn mir kein Punkt gesetzt wurde.",
     "Fehler.\nWenn mir kein Punkt gesetzt wurde."),
    ("Erster Satz. Neuer Absatz. Zweiter Satz.",
     "Erster Satz.\n\nZweiter Satz."),
    ("Ich komme morgen Punkt", "Ich komme morgen."),
    # the words stay words
    ("Das bringt es auf den Punkt.", "Das bringt es auf den Punkt."),
    ("Das ist ein guter Punkt.", "Das ist ein guter Punkt."),
    ("Treffen um Punkt 12 Uhr.", "Treffen um Punkt 12 Uhr."),
    ("Ich brauche eine neue Zeile in der Tabelle.",
     "Ich brauche eine neue Zeile in der Tabelle."),
])
def test_spoken_marks_inside_a_part(spoken, expected):
    import commands_de as cde
    assert cde.apply_inline_punctuation(spoken) == expected


def test_a_line_break_at_the_end_of_a_part_arrives(app):
    import dictation_window as dw
    win = dw.DictationWindow(on_insert=lambda text: True,
                             on_copy=lambda text: None)
    win._on_transcript("Ich habe folgenden Fehler Punkt. Neue Zeile.",
                       "auto", [])
    win._on_transcript("Wenn mir kein Punkt gesetzt wurde.", "auto", [])
    assert win.text() == ("Ich habe folgenden Fehler.\n"
                          "Wenn mir kein Punkt gesetzt wurde.")
    win.deleteLater()


# -- "Streich das": take the last part back to speak it again ------------------

def _window(app):
    import dictation_window as dw
    return dw.DictationWindow(on_insert=lambda text: True,
                              on_copy=lambda text: None)


def test_streich_das_removes_the_last_part(app):
    win = _window(app)
    win._on_transcript("Erster Satz.", "auto", [])
    win._on_transcript("Der falsch erkannte Satz.", "auto", [])
    win._on_transcript("Streich das.", "auto", [])
    assert win.text() == "Erster Satz."
    win._on_transcript("Der richtige Satz.", "auto", [])
    assert win.text() == "Erster Satz. Der richtige Satz."
    win.deleteLater()


def test_said_again_it_goes_back_part_by_part(app):
    win = _window(app)
    for part in ("Eins.", "Zwei.", "Drei."):
        win._on_transcript(part, "auto", [])
    win._on_transcript("Streich das.", "auto", [])
    win._on_transcript("Streiche das.", "auto", [])
    assert win.text() == "Eins."
    win._on_transcript("Weg damit.", "auto", [])
    assert win.text() == ""
    win._on_transcript("Streich das.", "auto", [])      # nothing left
    assert win.text() == ""
    win.deleteLater()


def test_a_full_stop_taken_back_after_a_pause_returns(app):
    win = _window(app)
    win._on_transcript("Ich komme morgen.", "auto", [])
    win._on_transcript("Weil es regnet.", "auto", [])
    assert win.text() == "Ich komme morgen, weil es regnet."
    win._on_transcript("Streich das.", "auto", [])
    assert win.text() == "Ich komme morgen."
    win.deleteLater()


def test_a_part_edited_meanwhile_is_left_alone(app):
    win = _window(app)
    win._on_transcript("Hallo Welt.", "auto", [])
    win._edit.setPlainText("Hallo schöne Welt.")      # edited by hand
    win._on_transcript("Streich das.", "auto", [])
    assert win.text() == "Hallo schöne Welt."
    win.deleteLater()


def test_a_part_dictated_into_the_middle_leaves_one_space(app):
    import editor_actions as ea
    from PySide6.QtWidgets import QPlainTextEdit
    edit = QPlainTextEdit()
    edit.setPlainText("Hallo Welt")
    cursor = edit.textCursor()
    cursor.setPosition(6)
    edit.setTextCursor(cursor)
    editor = ea.Editor(edit)
    editor.insert_dictation("schöne")
    assert edit.toPlainText() == "Hallo schöne Welt"
    import commands_de as cde
    editor.apply(cde.parse("streich das"))
    assert edit.toPlainText() == "Hallo Welt"
    edit.deleteLater()


# -- a spoken mark after a part takes the place of the automatic one -------------

@pytest.mark.parametrize("parts, expected", [
    # from a kept error report: "Fragezeichen" said as a part of its own
    (["Also, dass ich im Diktiermodus auch Befehle benutzen kann.", "?"],
     "Also, dass ich im Diktiermodus auch Befehle benutzen kann?"),
    (["Kommst du morgen.", "Fragezeichen."], "Kommst du morgen?"),
    (["Das ist toll.", "Ausrufezeichen."], "Das ist toll!"),
    (["Wir treffen uns am 16.", "?"], "Wir treffen uns am 16.?"),
    (["Das war's...", "?"], "Das war's...?"),
])
def test_a_spoken_mark_replaces_the_automatic_one(app, parts, expected):
    win = _window(app)
    for part in parts:
        win._on_transcript(part, "text", [])
    assert win.text() == expected
    win._on_transcript("Streich das.", "auto", [])     # and it comes back
    assert win.text() == parts[0]
    win.deleteLater()


def test_the_command_form_replaces_it_too(app):
    win = _window(app)
    win._on_transcript("Kommst du morgen.", "auto", [])
    win._on_transcript("Fragezeichen.", "auto", [])     # parsed as a command
    assert win.text() == "Kommst du morgen?"
    win.deleteLater()


# -- commands with the dictation key --------------------------------------------

def test_the_dictation_key_takes_commands_only_when_wanted(module, monkeypatch):
    import threading

    import module as dic
    monkeypatch.setattr(threading, "Thread",
                        lambda target, **_k: type(
                            "T", (), {"start": lambda self: None})())
    module._trigger, module._command_trigger = "Key.num_add", "ctrl+Key.num_0"
    monkeypatch.setattr(dic, "current_combo_str", lambda vk: "Key.num_add")
    module._state = "idle"
    module._on_key_event(0x6B, 0, False, False, True)
    assert module._active_mode == "text"          # as before: text only
    module._settings["commands_in_dictation"] = True
    module._on_key_event(0x6B, 0, False, False, True)
    assert module._active_mode == "mixed"         # a command alone counts


# -- spaces and spoken commas (from a kept error report) --------------------------

@pytest.mark.parametrize("parts, expected", [
    (["Bei so Befehlen wie Anführungsstriche unten kopieren Anführungsstriche "
      "oben.", "Macht zum Beispiel ein Dragon etwas anders."],
     "Bei so Befehlen wie „kopieren“ macht zum Beispiel ein Dragon etwas "
     "anders."),
    (["so etwas wie Anführungsstriche unten, Text kopieren, Anführungsstriche "
      "oben.", "Oder so etwas wie"],
     "so etwas wie „Text kopieren“ oder so etwas wie"),
    (["einen Doppelte Wörter Befehl.", "Komma, damit es nicht vorkommt."],
     "einen Doppelte Wörter Befehl, damit es nicht vorkommt."),
    (["einen Doppelte Wörter Befehl, Komma, damit es nicht vorkommt."],
     "einen Doppelte Wörter Befehl, damit es nicht vorkommt."),
    (["Punkt 12 Uhr treffen wir uns."], "Punkt 12 Uhr treffen wir uns."),
    (["Der Punkt, Komma, ist wichtig."], "Der Punkt, ist wichtig."),
])
def test_spaces_and_spoken_commas(app, parts, expected):
    win = _window(app)
    for part in parts:
        win._on_transcript(part, "text", [])
    assert win.text() == expected
    win.deleteLater()


# -- the dictation key with commands: one-word commands need their long form -------

def test_a_single_word_stays_text_with_the_dictation_key(app):
    import dictation_window as dw
    copied = []
    win = dw.DictationWindow(on_insert=lambda text: True,
                             on_copy=copied.append)
    win._on_transcript("Was soll ich tun?", "mixed", [])
    win._on_transcript("Kopieren.", "mixed", [])
    assert copied == [] and win.text().endswith("Kopieren.")
    win._on_transcript("Text kopieren.", "mixed", [])
    assert copied and "Text kopieren" not in win.text()
    win.deleteLater()


def test_marks_and_undo_work_alone_with_the_dictation_key(app):
    win = _window(app)
    win._on_transcript("Kommst du morgen.", "mixed", [])
    win._on_transcript("Fragezeichen.", "mixed", [])
    assert win.text() == "Kommst du morgen?"
    win._on_transcript("Streich das.", "mixed", [])
    assert win.text() == "Kommst du morgen."
    win.deleteLater()


def test_the_command_key_still_takes_one_word(app):
    import dictation_window as dw
    copied = []
    win = dw.DictationWindow(on_insert=lambda text: True,
                             on_copy=copied.append)
    win._edit.setPlainText("Hallo")
    win._on_transcript("Kopieren.", "command", [])
    assert copied == ["Hallo"]
    win.deleteLater()


# -- "Text einfügen" pastes, "Text übernehmen" hands over ---------------------------

def test_text_einfuegen_pastes_the_clipboard(app):
    from PySide6.QtWidgets import QApplication
    import dictation_window as dw
    inserted = []
    win = dw.DictationWindow(on_insert=lambda text: inserted.append(text) or True,
                             on_copy=lambda text: None)
    QApplication.clipboard().setText("aus der Zwischenablage")
    win._on_transcript("Hier kommt", "text", [])
    win._on_transcript("Text einfügen.", "mixed", [])
    assert win.text() == "Hier kommt aus der Zwischenablage"
    assert inserted == []                          # nothing handed over
    win._on_transcript("Streich das.", "mixed", [])
    assert win.text() == "Hier kommt"
    win.deleteLater()


def test_text_uebernehmen_hands_the_text_over(app):
    import dictation_window as dw
    inserted = []
    win = dw.DictationWindow(on_insert=lambda text: inserted.append(text) or True,
                             on_copy=lambda text: None)
    win._on_transcript("Ein Satz.", "text", [])
    win._on_transcript("Text übernehmen.", "mixed", [])
    assert inserted == ["Ein Satz."]
    win.deleteLater()


# -- the first look: command (blue) or dictation (green) ---------------------------

def _live_feed(session, audio, step=0.1):
    import time
    for i in range(0, len(audio), int(step * st.RATE) * 2):
        session.put(audio[i:i + int(step * st.RATE) * 2])
        time.sleep(0.01)


def test_a_preview_comes_after_a_short_quiet_and_is_reused(module):
    import time
    module._settings["segment_pause"] = 1.5
    kinds = []
    module._window.pause_kind = kinds.append
    session = module._make_segmenter()
    session.start()
    _live_feed(session, _tone(0.8) + _silence(1.0))
    deadline = time.monotonic() + 5
    while not kinds and time.monotonic() < deadline:
        time.sleep(0.02)
    assert kinds == ["text"]                  # "Teil 1." is no command
    _live_feed(session, _silence(0.8))        # stays quiet: the part ends
    module._segmenter = session
    module._state = "recording"
    module._stop_segmented()
    assert module._window.got == [("Teil 1.", "auto")]
    assert len(module.heard) == 1, "the preview was the result"


def test_speaking_on_after_the_preview_recognises_again(module):
    import time
    module._settings["segment_pause"] = 1.5
    module._window.pause_kind = lambda kind: None
    session = module._make_segmenter()
    session.start()
    _live_feed(session, _tone(0.8) + _silence(0.9))
    time.sleep(0.3)
    _live_feed(session, _tone(0.6) + _silence(0.2))
    module._segmenter = session
    module._state = "recording"
    module._stop_segmented()
    assert len(module.heard) == 2             # preview, then the whole part
    assert module._window.got == [("Teil 2.", "auto")]


def test_a_command_turns_the_preview_blue(module, monkeypatch):
    kinds = []
    module._window.pause_kind = kinds.append
    session = module._make_segmenter()
    module._active_mode = "mixed"
    module._on_part_preview(session, "Streich das.")
    module._on_part_preview(session, "Kopieren.")        # one word: text
    module._on_part_preview(session, "Ein ganz normaler Satz.")
    module._active_mode = "text"
    module._on_part_preview(session, "Streich das.")     # text key: text
    assert kinds == ["command", "text", "text", "text"]


def test_the_dot_is_blue_for_a_command_and_green_again_after(app):
    win = _window(app)
    win._apply_state("recording")
    win._apply_pause(1.5, 2.0)
    dot = win._pause_dot
    assert not dot.is_command()
    dot.set_kind("command")
    assert dot.is_command()
    win._apply_pause(-1.0, 2.0)                # the part is done
    win._apply_pause(1.9, 2.0)                 # the next pause
    assert not dot.is_command()
    win.deleteLater()


def test_the_chip_says_command(app):
    import module as dic
    chip = dic.DictationIndicator()
    chip._apply_state("recording", "")
    chip._apply_pause(1.2, 2.0)
    width = chip.width()
    chip._apply_kind("command")
    assert chip._subtitle().startswith("Befehl") or \
        chip._subtitle().startswith("Command")
    assert chip.width() == width
    chip._apply_pause(-1.0, 2.0)
    chip._apply_pause(1.0, 2.0)
    assert not chip._pause_command
    chip.deleteLater()


# -- digits said one by one (from a kept error report) --------------------------------

@pytest.mark.parametrize("said, written", [
    ("Eins, acht, acht, sieben.", "1887."),
    ("Die PIN ist vier acht eins null.", "Die PIN ist 4810."),
    ("Meine Nummer ist null, sieben, eins, eins, vier, fünf.",
     "Meine Nummer ist 071145."),
    ("drei, vier, fünf Mal", "drei, vier, fünf Mal"),     # a guess, no number
    ("Eins, zwei, drei", "Eins, zwei, drei"),
    ("Es sind acht Stück.", "Es sind acht Stück."),
    ("Eins zu null gewonnen.", "Eins zu null gewonnen."),
    # a postcode Whisper wrote as single figures, with the town after it
    ("6, 8, 1, 9, 9, Mannheim.", "68199 Mannheim"),
    ("Sechs acht eins neun neun Mannheim", "68199 Mannheim"),
    ("67433 Neustadt an der Weinstraße.", "67433 Neustadt an der Weinstraße"),
    ("Ich wohne in 68199 Mannheim.", "Ich wohne in 68199 Mannheim."),
    ("Meine PIN ist 1 2 3 4.", "Meine PIN ist 1234."),
    ("Das kostet 3,5 Euro.", "Das kostet 3,5 Euro."),
    ("Punkte 1, 2 und 3.", "Punkte 1, 2 und 3."),
])
def test_digits_said_one_by_one_become_a_number(said, written):
    from postprocess import fix_digit_sequences
    assert fix_digit_sequences(said) == written


# -- a command runs as soon as the first look has seen it --------------------------------

def test_a_command_ends_the_part_without_the_whole_pause(module):
    import time
    module._settings["segment_pause"] = 3.0
    module._active_mode = "mixed"
    module._window.pause_kind = lambda kind: None
    module.transcribe = lambda wav: (module.heard.append(len(wav))
                                     or "Streich das.")
    session = module._make_segmenter()
    session.start()
    started = time.monotonic()
    _live_feed(session, _tone(0.8) + _silence(1.2))
    deadline = time.monotonic() + 5
    while not module._window.got and time.monotonic() < deadline:
        time.sleep(0.02)
    took = time.monotonic() - started
    assert module._window.got == [("Streich das.", "mixed")]
    assert took < 2.5, "a 3 s pause was not waited for"
    assert len(module.heard) == 1                 # the first look was reused
    session.stop(timeout=5)


def test_dictation_still_waits_for_the_whole_pause(module):
    import time
    module._settings["segment_pause"] = 3.0
    module._active_mode = "mixed"
    module._window.pause_kind = lambda kind: None
    session = module._make_segmenter()
    session.start()
    _live_feed(session, _tone(0.8) + _silence(1.5))
    time.sleep(0.3)
    assert module._window.got == []               # "Teil 1." is no command
    session.stop(timeout=5)


# -- dates: day and month always two digits -------------------------------------------

@pytest.mark.parametrize("said, written", [
    ("Wohlmöglich 6.10.2026 gemeint", "Wohlmöglich 06.10.2026 gemeint"),
    ("Am 6.10. treffen wir uns.", "Am 06.10. treffen wir uns."),
    ("am 1.2.26 war es kalt", "am 01.02.26 war es kalt"),
    ("Am 6. Oktober 2026.", "Am 06.10.2026."),
    ("Termin 31.12.2026.", "Termin 31.12.2026."),
    # no dates
    ("um 6.10 Uhr", "um 6.10 Uhr"),
    ("Kapitel 6.10. lesen", "Kapitel 6.10. lesen"),
    ("Version 1.2.3", "Version 1.2.3"),
    ("IP 10.1.1.20", "IP 10.1.1.20"),
    ("der 32.1.2026", "der 32.1.2026"),
])
def test_dates_get_two_digit_day_and_month(said, written):
    from postprocess import fix_dates
    assert fix_dates(said) == written


# -- a much quieter voice (the TV) is not you ---------------------------------------

def test_a_much_quieter_voice_is_dropped_and_kept_for_reports(module, monkeypatch):
    monkeypatch.setattr(module, "on_settings_changed", lambda: None)
    module._settings["segment_pause"] = 1.0
    module._window.pause_kind = lambda kind: None
    session = module._make_segmenter()
    session.start()
    _live_feed(session, _tone(1.0, amplitude=8000) + _silence(1.3))  # you
    _live_feed(session, _tone(1.0, amplitude=600) + _silence(1.3))   # the TV
    module._segmenter = session
    module._state = "recording"
    module._stop_segmented()
    assert module._window.got == [("Teil 1.", "auto")]
    assert len(module.heard) == 1                 # the TV never reached Whisper
    dropped = module._recent_parts[-1]
    assert dropped["raw"] == "" and "leiser" in dropped["text"]
    assert module._settings["voice_level"] > 3000  # your level, remembered


def test_the_learned_level_is_used_from_the_first_part(module):
    module._settings["voice_level"] = 7000
    session = module._make_segmenter()
    assert session.voice_level == 7000 and session.background_ratio == 0.25


# -- a part that continues a sentence starts in lower case (from a kept report) -------

def test_verbs_continue_a_sentence_in_lower_case(app):
    win = _window(app)
    for part in ("Auch bei diesem Diktat", "Habe ich wieder ein Fehler gemerkt?",
                 "Komma", "Schaue ihn dir an.", "Das Leben ist schön."):
        win._on_transcript(part, "mixed", [])
    assert win.text() == ("Auch bei diesem Diktat habe ich wieder ein Fehler "
                          "gemerkt, schaue ihn dir an. Das Leben ist schön.")
    win.deleteLater()


def test_a_dropped_part_has_no_uncertain_words_of_its_own(module):
    module._last_low_words = ["Ihnen"]
    module._remember_skipped(b"\x00\x00" * 1600, "quieter than your voice")
    assert module._recent_parts[-1]["low"] == []


# -- one word for both quotation marks ------------------------------------------------

@pytest.mark.parametrize("parts, expected", [
    (["Er sagte Anführungsstriche Hallo Anführungsstriche und ging."],
     "Er sagte „Hallo“ und ging."),
    (["Er sagte", "Anführungsstriche.", "Hallo.", "Anführungsstriche.",
      "Dann ging er."],
     "Er sagte „Hallo.“ Dann ging er."),
    (["Das Wort Anführungsstriche kopieren Anführungsstriche ist ein Befehl."],
     "Das Wort „kopieren“ ist ein Befehl."),
    # the spoken side still decides
    (["Anführungsstriche unten Test Anführungsstriche oben."], "„Test“"),
    (["Er rief Anführungsstriche unten Hilfe", "Anführungsstriche."],
     "Er rief „Hilfe“"),
])
def test_one_word_for_both_quotation_marks(app, parts, expected):
    win = _window(app)
    for part in parts:
        win._on_transcript(part, "text", [])
    assert win.text() == expected
    win.deleteLater()


def test_the_open_quote_is_read_from_the_text_before_the_cursor():
    import commands_de as cde
    assert cde.resolve_bare_quotes("Anführungsstriche", "Er sagte „Hallo") \
        == "Anführungsstriche oben"
    assert cde.resolve_bare_quotes("Anführungsstriche", "Er sagte „Hallo“") \
        == "Anführungsstriche unten"
    assert cde.resolve_bare_quotes("Anführerstriche", "") \
        == "Anführerstriche unten"


def test_the_pronunciation_training_stays_out_of_a_running_dictation(
        module, monkeypatch):
    """Training a word with the dictation still on: the repetitions must
    not end up in the dictation window."""
    import module as dic
    got = {}

    class _Stream:
        def stop(self):
            pass

        def close(self):
            pass

    def open_stream(_sd, _device, callback):
        got["callback"] = callback
        return _Stream(), st.RATE, 1
    monkeypatch.setattr(dic, "open_input_stream", open_stream)
    monkeypatch.setattr(dic, "resolve_input_device", lambda *_a: None)
    monkeypatch.setattr(module, "_capture_target", lambda: None)
    module._window.request_open = lambda: None
    fed = []
    real_make = module._make_segmenter

    def make():
        session = real_make()
        put = session.put
        session.put = lambda pcm: (fed.append(pcm), put(pcm))
        return session
    monkeypatch.setattr(module, "_make_segmenter", make)
    module._start_recording()
    assert module._state == "recording"
    monkeypatch.setattr(module, "_open_mic_16k",
                        lambda _sd, _feed: (_Stream(), st.RATE, 1))
    capture = module.capture_takes(lambda _pcm: None)
    speech = _tone(0.1)
    got["callback"](speech, 0, None, None)
    assert fed[-1] == bytes(len(speech))          # the dictation hears silence
    capture.stop()
    capture.stop()                                # twice does no harm
    assert module._mic_borrowed == 0
    got["callback"](speech, 0, None, None)
    assert fed[-1] == speech                      # and then listens again
    module._stop_segmented()

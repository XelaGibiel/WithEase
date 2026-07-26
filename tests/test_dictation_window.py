"""Tests for the dictation window's transcript routing (examples/dictation)."""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import dictation_window as dw  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make(app, history=None, hist_sink=None):
    inserted, copied = [], []
    win = dw.DictationWindow(
        on_insert=inserted.append, on_copy=copied.append,
        on_history_changed=(hist_sink if hist_sink is not None else None),
        history=history)
    return win, inserted, copied


def feed(app, win, text, mode="auto"):
    win.handle_transcript(text, mode)
    app.processEvents()


def test_dictation_then_command_then_correction(app):
    win, inserted, _ = make(app)
    feed(app, win, "Hallo Welt")
    feed(app, win, "neue Zeile")
    feed(app, win, "Zeile zwei")
    assert win.text() == "Hallo Welt\nZeile zwei"
    # correction: select a word and re-speak
    feed(app, win, "markiere Welt")
    feed(app, win, "Erde")
    assert win.text() == "Hallo Erde\nZeile zwei"


def test_insert_command_inserts_then_closes_and_clears(app):
    win, inserted, _ = make(app)
    win.show()
    feed(app, win, "Guten Tag")
    feed(app, win, "einfügen")
    assert inserted == ["Guten Tag"]
    assert win.text() == ""              # buffer cleared
    assert not win.isVisible()           # window closed
    assert win._history.count() == 1     # text kept in the history
    assert win._history.item(0).data(dw.Qt.ItemDataRole.UserRole) == "Guten Tag"


def test_copy_command_calls_callback(app):
    win, _, copied = make(app)
    feed(app, win, "Notiz")
    feed(app, win, "kopieren")
    assert copied == ["Notiz"]


def test_spell_inline(app):
    win, _, _ = make(app)
    feed(app, win, "buchstabiere Ludwig Emil Ida")   # L E I
    assert win.text() == "Lei"


def test_ambiguous_marks_candidates(app):
    win, _, _ = make(app)
    feed(app, win, "Haus und Haus")
    feed(app, win, "markiere Haus")
    assert len(win._edit.extraSelections()) == 2      # both matches highlighted
    assert len(win._badges._badges) == 2              # numbered badges painted
    assert "Treffer" in win._hint.text()
    feed(app, win, "nimm zwei")
    assert win._edit.extraSelections() == []          # cleared after pick
    assert win._badges._badges == []


def test_command_mode_does_not_insert_unknown(app):
    win, _, _ = make(app)
    feed(app, win, "irgendein Satz", mode="command")
    assert win.text() == ""                           # not dumped as text
    assert "nicht erkannt" in win._hint.text()
    feed(app, win, "neue Zeile", mode="command")      # a real command still runs
    assert win.text() == "\n"


def test_text_mode_never_runs_commands(app):
    win, _, _ = make(app)
    feed(app, win, "markiere Haus", mode="text")      # looks like a command
    assert win.text() == "markiere Haus"              # inserted verbatim


def test_history_persists_and_caps_fifo(app):
    saved = []
    win, _, _ = make(app, hist_sink=lambda items: saved.__setitem__(
        slice(None), items))
    for i in range(25):
        feed(app, win, f"Eintrag {i}")
        feed(app, win, "schließen")
    assert win._history.count() == 20                 # capped
    assert len(saved) == 20                            # persisted list capped
    assert saved[0] == "Eintrag 24"                    # newest first
    assert "Eintrag 4" not in saved                    # oldest dropped


def test_history_restored_from_storage(app):
    win, _, _ = make(app, history=["Zweiter", "Erster"])   # newest first
    assert win._history.count() == 2
    win._load_history(win._history.item(0))
    app.processEvents()
    assert win.text() == "Zweiter"


def test_spell_mode(app):
    win, _, _ = make(app)
    feed(app, win, "buchstabieren")
    feed(app, win, "Heinrich Anton Ulrich Samuel")   # H A U S
    assert win.text() == "Haus"


def test_close_clears_buffer_and_archives(app):
    win, _, _ = make(app)
    feed(app, win, "Hallo Welt")
    feed(app, win, "schließen")
    assert win.text() == ""                       # buffer cleared on close
    assert win._history.count() == 1              # text moved to history
    assert win._history.item(0).data(dw.Qt.ItemDataRole.UserRole) == "Hallo Welt"


def test_copy_and_close(app):
    win, _, copied = make(app)
    feed(app, win, "Meine Notiz")
    win._do_copy_and_close()
    app.processEvents()
    assert copied == ["Meine Notiz"]
    assert win.text() == ""


def test_history_reload(app):
    win, _, _ = make(app)
    feed(app, win, "Erster Text")
    feed(app, win, "schließen")
    assert win.text() == ""
    win._load_history(win._history.item(0))
    app.processEvents()
    assert win.text() == "Erster Text"


def test_correction_window_opens_speak_and_confirm(app):
    learned = []
    win = dw.DictationWindow(
        on_correction=lambda old, new: learned.append((old, new)))
    win.handle_transcript("Hallo Welt", "text")
    win.handle_transcript("korrigiere Welt", "command")
    app.processEvents()
    assert win._correction_dialog is not None            # correction window open
    assert win._correction_dialog.result_text() == "Welt"  # prefilled with target
    win.handle_transcript("Erde", "text")                # spoken → fills the field
    app.processEvents()
    assert win._correction_dialog.result_text() == "Erde"
    win.handle_transcript("übernehmen", "command")       # confirm hands-free
    app.processEvents()
    assert win._correction_dialog is None
    assert win.text() == "Hallo Erde"
    assert learned == [("Welt", "Erde")]


def test_correction_window_spoken_word_has_no_trailing_period(app):
    win, _, _ = make(app)
    win.handle_transcript("Ich sehe ein Haus", "text")
    win.handle_transcript("korrigiere Haus", "command")
    app.processEvents()
    win.handle_transcript("Auto.", "text")          # Whisper appended a period
    app.processEvents()
    assert win._correction_dialog.result_text() == "Auto"   # no trailing period
    win.handle_transcript("übernehmen", "command")
    app.processEvents()
    assert win.text() == "Ich sehe ein Auto"


def test_correction_window_typed_self_input(app):
    learned = []
    win = dw.DictationWindow(
        on_correction=lambda old, new: learned.append((old, new)))
    win.handle_transcript("Ich sehe ein Haus", "text")
    win.handle_transcript("korrigiere Haus", "command")
    app.processEvents()
    dlg = win._correction_dialog
    assert dlg is not None
    dlg._field.setText("Auto")       # user types the correction (self-input)
    dlg._apply()                     # clicks "Übernehmen"
    app.processEvents()
    assert win._correction_dialog is None
    assert win.text() == "Ich sehe ein Auto"
    assert learned == [("Haus", "Auto")]


def test_correct_das_uses_manual_selection(app):
    win, _, _ = make(app)
    win.handle_transcript("Ein schönes Haus", "text")
    app.processEvents()
    # simulate a manual (mouse) selection of just "schönes"
    cur = win._edit.textCursor()
    cur.setPosition(4)
    cur.setPosition(11, dw.QTextCursor.MoveMode.KeepAnchor)
    win._edit.setTextCursor(cur)
    assert cur.selectedText() == "schönes"
    win.handle_transcript("korrigiere das", "command")
    app.processEvents()
    assert win._correction_dialog is not None
    assert win._correction_dialog.result_text() == "schönes"   # only that word
    win._correction_dialog._field.setText("kleines")
    win._correction_dialog._apply()
    app.processEvents()
    assert win.text() == "Ein kleines Haus"


def test_correction_window_cancel_keeps_text(app):
    win, _, _ = make(app)
    win.handle_transcript("Hallo Welt", "text")
    win.handle_transcript("korrigiere Welt", "command")
    app.processEvents()
    win.handle_transcript("abbrechen", "command")
    app.processEvents()
    assert win._correction_dialog is None
    assert win.text() == "Hallo Welt"                    # unchanged


def test_marking_then_respeak_does_not_learn(app):
    learned = []
    win = dw.DictationWindow(
        on_correction=lambda old, new: learned.append((old, new)))
    win.handle_transcript("Hallo Welt", "text")
    win.handle_transcript("markiere Welt", "command")     # just a quick edit
    win.handle_transcript("Erde", "text")
    app.processEvents()
    assert win.text() == "Hallo Erde"
    assert learned == []                                   # must NOT learn


def test_replace_command_forwards_correction(app):
    learned = []
    win = dw.DictationWindow(
        on_correction=lambda old, new: learned.append((old, new)))
    win.handle_transcript("Ich mag Katzen", "text")
    win.handle_transcript("ersetze Katzen durch Hunde", "command")
    app.processEvents()
    assert win.text() == "Ich mag Hunde"
    assert learned == [("Katzen", "Hunde")]


def test_state_shows_mode(app):
    win, _, _ = make(app)
    win.set_state("recording", "Befehl")
    app.processEvents()
    assert "Aufnahme" in win._status.text()
    assert "Befehl" in win._status.text()
    win.set_state("transcribing", "Diktat")
    app.processEvents()
    assert "Diktat" in win._status.text()


def test_state_indicator_updates(app):
    win, _, _ = make(app)
    win.set_state("recording")
    app.processEvents()
    assert "Aufnahme" in win._status.text()
    win.set_state("transcribing")
    app.processEvents()
    assert "erkannt" in win._status.text()
    win.set_state("idle")
    app.processEvents()
    assert "Bereit" in win._status.text()

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

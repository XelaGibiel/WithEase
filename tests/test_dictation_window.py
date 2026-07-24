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


def make(app):
    inserted, copied = [], []
    win = dw.DictationWindow(on_insert=inserted.append, on_copy=copied.append)
    return win, inserted, copied


def feed(app, win, text):
    win.handle_transcript(text)
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


def test_insert_command_calls_callback(app):
    win, inserted, _ = make(app)
    feed(app, win, "Guten Tag")
    feed(app, win, "einfügen")
    assert inserted == ["Guten Tag"]


def test_copy_command_calls_callback(app):
    win, _, copied = make(app)
    feed(app, win, "Notiz")
    feed(app, win, "kopieren")
    assert copied == ["Notiz"]


def test_spell_mode(app):
    win, _, _ = make(app)
    feed(app, win, "buchstabieren")
    feed(app, win, "Heinrich Anton Ulrich Samuel")   # H A U S
    assert win.text() == "Haus"


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

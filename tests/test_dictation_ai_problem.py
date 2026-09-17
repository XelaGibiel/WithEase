"""A failed KI-Aktion has to be noticed.

Reported from real use: choosing a KI-Aktion without Ollama running only put a
line into the status bar at the bottom - where nobody looks right after
choosing an action.  It now shows a red chip in the middle of the text, in
the same place as "KI arbeitet …", and says what to do.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

import requests  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import dictation_window as dw  # noqa: E402
import module as dic  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _german():
    from withease.core.event_bus import bus
    import dict_i18n
    before = dict_i18n._lang.code
    bus.publish("i18n.language_changed", lang="de")
    yield
    bus.publish("i18n.language_changed", lang=before)


@pytest.fixture
def win(app):
    w = dw.DictationWindow(on_insert=lambda _t: None, on_copy=lambda _t: None)
    w.resize(700, 500)
    w.show()
    app.processEvents()
    yield w
    w.close()


def _problem(app, win, kind, detail=""):
    win.ai_problem(kind, detail)
    app.processEvents()


def test_ollama_not_running_is_shown_over_the_text(app, win):
    win.ai_busy(True)
    app.processEvents()
    _problem(app, win, "ollama")

    chip = win._problem_chip
    assert chip.isVisible()
    assert "Ollama läuft nicht" in chip.text()
    assert "Starte Ollama" in chip.text()
    assert not win._busy_chip.isVisible()
    assert all(b.isEnabled() for b in win._ai_buttons)
    # centred over the editor
    r = win._edit.viewport().rect()
    assert abs(chip.geometry().center().x() - r.center().x()) <= 2
    # and the status line keeps it for later
    assert "Ollama läuft nicht" in win._hint.text()


def test_a_click_closes_it(app, win):
    _problem(app, win, "ollama")
    chip = win._problem_chip
    pos = QPointF(chip.width() / 2, chip.height() / 2)
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        QApplication.sendEvent(chip, QMouseEvent(
            kind, pos, chip.mapToGlobal(pos), Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert not chip.isVisible()


def test_editing_or_trying_again_closes_it(app, win):
    _problem(app, win, "ollama")
    win._edit.insertPlainText("x")
    assert not win._problem_chip.isVisible()

    _problem(app, win, "ollama")
    win.ai_busy(True)
    app.processEvents()
    assert not win._problem_chip.isVisible()


def test_it_goes_away_by_itself(app, win):
    _problem(app, win, "timeout")
    assert win._problem_timer.isActive()
    win._problem_timer.timeout.emit()
    assert not win._problem_chip.isVisible()


def test_an_unknown_error_shows_its_own_text_escaped(app, win):
    _problem(app, win, "other", "<b>kaputt</b>")
    assert "&lt;b&gt;kaputt" in win._problem_chip.text()
    _problem(app, win, "no-such-kind", "egal")
    assert "fehlgeschlagen" in win._problem_chip.text()


# -- naming the cause --------------------------------------------------------

@pytest.fixture
def module(app):
    return dic.DictationModule()


@pytest.mark.parametrize("provider, want", [
    ("ollama", "ollama"), ("lmstudio", "lmstudio")])
def test_a_refused_local_server_is_named(module, monkeypatch, provider, want):
    monkeypatch.setattr(module, "_ai_local_provider", lambda: provider)
    exc = requests.ConnectionError("Connection refused")
    assert module._ai_problem_kind(exc, "local") == (want, "")


@pytest.mark.parametrize("exc, backend, want", [
    (requests.ReadTimeout("slow"), "local", "timeout"),
    (requests.ConnectionError("no route"), "cloud", "offline"),
    (RuntimeError("cloud AI not configured"), "cloud", "setup"),
    (RuntimeError("model 'x' not found"), "local", "other"),
])
def test_other_causes(module, exc, backend, want):
    assert module._ai_problem_kind(exc, backend)[0] == want


def test_the_real_ollama_call_reports_it(app, module, monkeypatch):
    """End to end through run_ai_action, with the network refusing."""
    seen = []

    class _Window:
        def text(self):
            return "Hallo"

        def ai_busy(self, on=True):
            pass

        def ai_problem(self, kind, detail=""):
            seen.append(kind)

    def refuse(*_a, **_k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", refuse)
    monkeypatch.setattr(module, "_ai_local_provider", lambda: "ollama")
    module._settings["ai_backend"] = "local"
    module._window = _Window()

    import threading
    started = []
    monkeypatch.setattr(threading, "Thread",
                        lambda target, daemon=None: started.append(target)
                        or type("T", (), {"start": lambda self: target()})())
    module.run_ai_action("Korrigiere")
    assert seen == ["ollama"]

"""A recording can be stopped with the mouse, not only with Escape.

Escape has always aborted a running recording.  For someone who is at the
mouse precisely because the keyboard is the hard part, a keyboard-only way
out is no way out - and this program is written for exactly those people.
So the status chip grows a cancel pill while it records.
"""
import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from withease.core.event_bus import bus  # noqa: E402

import module as dic  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def chip(app):
    c = dic.DictationIndicator()
    yield c
    c.close()


@pytest.fixture
def cancels():
    seen = []

    def listen(**_):
        seen.append(1)

    bus.subscribe("dictation.cancel", listen)
    yield seen
    bus.unsubscribe("dictation.cancel", listen)


def click(chip, point: QPoint) -> None:
    chip.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(point), QPointF(point),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))


# -- where the pill is -------------------------------------------------------

def test_there_is_a_cancel_pill_while_recording(chip):
    chip._apply_state("recording", "Diktat")
    rect = chip._cancel_rect()
    assert rect is not None
    assert rect.width() > 0 and rect.height() == chip._chip_h


def test_it_sits_beside_the_chip_and_never_on_top_of_it(chip):
    chip._apply_state("recording", "Diktat")
    rect = chip._cancel_rect()
    assert rect.left() > chip._chip_w(), "it must not cover the status text"
    assert chip._content_w() >= chip._chip_w() + rect.width()


@pytest.mark.parametrize("state", ["transcribing", "loading", "error", "idle"])
def test_there_is_nothing_to_cancel_in_any_other_state(chip, state):
    chip._apply_state(state, "")
    assert chip._cancel_rect() is None


# -- clicking it -------------------------------------------------------------

def test_clicking_the_pill_asks_for_the_recording_to_stop(chip, cancels):
    chip._apply_state("recording", "Diktat")
    click(chip, chip._cancel_rect().center())
    assert cancels == [1]


def test_clicking_the_chip_itself_does_not_stop_anything(chip, cancels):
    chip._apply_state("recording", "Diktat")
    click(chip, QPoint(chip._chip_w() // 2, chip._chip_h // 2))
    assert cancels == []


def test_a_click_while_not_recording_does_not_stop_anything(chip, cancels):
    chip._apply_state("transcribing", "Diktat")
    click(chip, QPoint(10, 10))
    assert cancels == []


# -- what the module does with it -------------------------------------------

def test_the_module_drops_the_take(app, monkeypatch):
    m = dic.DictationModule()
    aborted = []
    monkeypatch.setattr(m, "_abort_recording", lambda: aborted.append(1))
    m._state = "recording"

    m._on_cancel_requested()
    for _ in range(50):                       # it runs on a thread of its own
        if aborted:
            break
        time.sleep(0.01)
    assert aborted == [1]


def test_nothing_happens_when_no_recording_is_running(app, monkeypatch):
    m = dic.DictationModule()
    aborted = []
    monkeypatch.setattr(m, "_abort_recording", lambda: aborted.append(1))
    m._state = "idle"

    m._on_cancel_requested()
    time.sleep(0.05)
    assert aborted == []


def test_the_chip_reaches_the_module_through_the_bus(app, monkeypatch, chip):
    """The pill and the module never see each other; the bus connects them."""
    m = dic.DictationModule()
    aborted = []
    monkeypatch.setattr(m, "_abort_recording", lambda: aborted.append(1))
    m._state = "recording"

    chip._apply_state("recording", "Diktat")
    click(chip, chip._cancel_rect().center())
    for _ in range(50):
        if aborted:
            break
        time.sleep(0.01)
    assert aborted == [1]

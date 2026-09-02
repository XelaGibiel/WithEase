"""Status chips must stay on top of every other always-on-top window.

Windows keeps a single "topmost" band in which the last window raised wins, so
opening the dictation window (or any other assistive tool that stays on top)
pushes the sticky-keys chip underneath it.  A status chip nobody can see is the
same as no chip at all – which is exactly what happened: the chip reported that
Sticky Ctrl was latched, from behind the window the user was typing into.

Showing without activating deliberately does NOT change the stacking position,
so the chip came back wherever it had been pushed to.  The coordinator now
re-raises the visible overlays: instantly when the foreground window changes
(the real trigger – clicking into another window), and on a slow tick as a
safety net for anything that reorders without taking focus.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from withease.gui.widgets import cursor_indicator as ci  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _FakeOverlay:
    def __init__(self, visible: bool = True) -> None:
        self._visible = visible
        self.raised = 0
        self.suppressed: bool | None = None

    def set_suppressed(self, value: bool) -> None:
        self.suppressed = value

    def isVisible(self) -> bool:  # noqa: N802 (Qt-style API)
        return self._visible

    def raise_(self) -> None:
        self.raised += 1


@pytest.fixture
def coordinator(monkeypatch):
    """A fresh coordinator with no fullscreen detection and a stable
    foreground window, so each test drives exactly one variable."""
    monkeypatch.setattr(ci.IndicatorCoordinator, "_instance", None)
    monkeypatch.setattr(ci, "foreground_is_fullscreen", lambda: False)
    monkeypatch.setattr(ci, "_foreground_window", lambda: 1)
    coord = ci.IndicatorCoordinator.get()
    coord._timer.stop()          # tick by hand, not on a real timer
    return coord


def test_a_changed_foreground_window_raises_the_chip_at_once(
        app, coordinator, monkeypatch):
    chip = _FakeOverlay()
    coordinator.register_suppressible(chip)
    coordinator._reposition()                 # first tick settles the baseline
    before = chip.raised

    monkeypatch.setattr(ci, "_foreground_window", lambda: 2)   # user clicks away
    coordinator._reposition()
    assert chip.raised == before + 1, (
        "clicking another window is what buries the chip – it must come back "
        "on the very next tick, not half a second later")


def test_the_slow_tick_recovers_without_a_focus_change(app, coordinator):
    """Something can reorder the topmost band without taking focus."""
    chip = _FakeOverlay()
    coordinator.register_suppressible(chip)
    coordinator._reposition()
    before = chip.raised
    for _ in range(31):
        coordinator._reposition()
    assert chip.raised > before


def test_a_hidden_chip_is_never_touched(app, coordinator, monkeypatch):
    hidden = _FakeOverlay(visible=False)
    coordinator.register_suppressible(hidden)
    monkeypatch.setattr(ci, "_foreground_window", lambda: 3)
    coordinator._reposition()
    assert hidden.raised == 0


def test_nothing_is_raised_over_a_fullscreen_window(app, coordinator,
                                                    monkeypatch):
    """A game or a video keeps the screen to itself – the chips hide instead."""
    chip = _FakeOverlay()
    coordinator.register_suppressible(chip)
    monkeypatch.setattr(ci, "foreground_is_fullscreen", lambda: True)
    coordinator._reposition()
    assert chip.suppressed is True
    assert chip.raised == 0

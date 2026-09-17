"""Caps Lock in the Sticky Keys chip.

Reported from real use: with Shift latched, "." still gave "." instead of ":"
in the dictation window.  Sticky Keys worked - Caps Lock was on.  On the
German layout Caps Lock + Shift + "." gives ".", and letters come out small,
so an accidental Caps Lock looks exactly like Sticky Keys failing.  The chip
that shows the latched keys now shows a switched-on Caps Lock as well.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from withease.core.event_bus import bus  # noqa: E402
from withease.gui.widgets import modifier_indicator as mi  # noqa: E402
from withease.modules import keyboard as kb  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def chip(app, monkeypatch):
    caps = {"on": False}
    monkeypatch.setattr(mi, "capslock_on", lambda: caps["on"])
    widget = mi.ModifierIndicator()
    widget.caps = caps
    yield widget
    widget._capslock_timer.stop()
    for topic, handler in (("keyboard.modifier_status", widget._on_status),
                           ("keyboard.indicator_position", widget._on_position),
                           ("keyboard.chip_size", widget._on_chip_size),
                           ("keyboard.preview", widget._on_preview),
                           ("keyboard.capslock_watch",
                            widget._on_capslock_watch)):
        bus.unsubscribe(topic, handler)
    # The app-wide overlay coordinator keeps every chip it was given; one
    # that is deleted after the test must not stay on its list.
    from withease.gui.widgets.cursor_indicator import IndicatorCoordinator
    suppressibles = IndicatorCoordinator.get()._suppressibles
    if widget in suppressibles:
        suppressibles.remove(widget)


def _names(widget):
    return [name for name, _colour in widget._chips()]


def test_caps_lock_shows_while_it_is_on(chip):
    chip._apply_capslock_watch(True)
    assert not chip.isVisible()

    chip.caps["on"] = True
    chip._poll_capslock()
    assert _names(chip) == ["capslock"]
    assert chip.isVisible()

    chip.caps["on"] = False
    chip._poll_capslock()
    assert not chip.isVisible()


def test_it_stands_behind_the_latched_keys(chip):
    chip._apply_capslock_watch(True)
    chip.caps["on"] = True
    chip._poll_capslock()
    chip._apply_state({"shift": True})
    assert _names(chip) == ["shift", "capslock"]


def test_nothing_is_shown_when_the_watch_is_off(chip):
    chip.caps["on"] = True
    chip._apply_capslock_watch(False)
    chip._poll_capslock()
    assert _names(chip) == []
    assert not chip.isVisible()


def test_turning_the_watch_off_hides_a_shown_chip(chip):
    chip._apply_capslock_watch(True)
    chip.caps["on"] = True
    chip._poll_capslock()
    assert chip.isVisible()
    chip._apply_capslock_watch(False)
    assert not chip.isVisible()


def test_the_longer_label_gets_a_wider_chip(chip):
    chip._apply_capslock_watch(True)
    chip.caps["on"] = True
    chip._poll_capslock()
    chip._apply_state({"shift": True})
    assert chip._width_of("capslock") >= chip._width_of("shift")
    assert chip.width() >= (chip._width_of("capslock")
                            + chip._width_of("shift"))


def test_the_preview_includes_it(chip):
    chip._apply_preview(True)
    assert _names(chip)[-1] == "capslock"
    chip._apply_preview(False)


# -- who switches the watch on ----------------------------------------------

@pytest.fixture
def watch():
    seen = []

    def listener(active, **_):
        seen.append(active)
    bus.subscribe("keyboard.capslock_watch", listener)
    yield seen
    bus.unsubscribe("keyboard.capslock_watch", listener)


def test_the_keyboard_module_switches_it(watch, monkeypatch):
    monkeypatch.setattr(kb.shared_keyboard_hook, "subscribe", lambda _cb: None)
    monkeypatch.setattr(kb.shared_keyboard_hook, "unsubscribe", lambda _cb: None)
    m = kb.KeyboardModule()
    m.load_settings({"sticky_enabled": True})
    assert watch[-1] is False, "not running yet"
    m.enable()
    assert watch[-1] is True
    m.disable()
    assert watch[-1] is False


@pytest.mark.parametrize("settings", [
    {"sticky_enabled": False},
    {"sticky_enabled": True, "capslock_indicator": False},
])
def test_it_stays_off_when_not_wanted(watch, monkeypatch, settings):
    monkeypatch.setattr(kb.shared_keyboard_hook, "subscribe", lambda _cb: None)
    monkeypatch.setattr(kb.shared_keyboard_hook, "unsubscribe", lambda _cb: None)
    m = kb.KeyboardModule()
    m.load_settings(settings)
    m.enable()
    assert watch[-1] is False
    m.disable()


def test_the_settings_page_has_the_switch(app, monkeypatch):
    m = kb.KeyboardModule()
    m.load_settings({"sticky_enabled": True})
    page = m.get_settings_widget()
    assert page._capslock_cb.isChecked()
    page._capslock_cb.setChecked(False)
    assert m.dump_settings()["capslock_indicator"] is False
    page.deleteLater()

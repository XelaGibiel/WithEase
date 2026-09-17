"""The tray names the microphone in use and lets you switch it.

The core tray knows nothing about audio.  It asks: "tray.tooltip" collects
extra lines for the hover text, "tray.menu" collects submenus.  The dictation
module answers with the microphone - so the question "which microphone is
dictation listening to?" no longer needs the settings, and neither does
changing it.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from withease.core.event_bus import bus  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_bus():
    """Every TrayIcon subscribes to the global bus for the life of the app.
    In a test run that means each test's tray keeps reacting in every later
    test - so the bus is put back the way it was after each one."""
    saved = {event: list(callbacks)
             for event, callbacks in bus._listeners.items()}
    # Every DictationModule built by an earlier test file answered the
    # tray too, adding its own microphone line - enough of them push past
    # the tooltip limit and crowd out the line under test.  Start clean.
    for event in ("tray.tooltip", "tray.menu", "tray.refresh"):
        bus._listeners[event] = []
    yield
    for event in list(bus._listeners):
        bus._listeners[event] = list(saved.get(event, []))


class _FakeApp:
    """Just enough of WithEaseApp for the tray to build its menu."""
    is_paused = False
    _paused = False
    active_profile = "Default"

    def get_modules(self):
        return []

    def list_profiles(self):
        return ["Default"]

    def switch_profile(self, _name): ...
    def show_settings(self, *_a): ...
    def pause_all(self): ...
    def resume_all(self): ...
    def quit(self): ...


@pytest.fixture
def subscribe():
    """Subscribe to the global bus for one test only."""
    added = []

    def _sub(event, callback):
        bus.subscribe(event, callback)
        added.append((event, callback))

    yield _sub
    for event, callback in added:
        bus.unsubscribe(event, callback)


def _tray(app):
    from withease.tray import TrayIcon
    return TrayIcon(_FakeApp())


def _submenu(tray, title):
    """The submenu called ``title``.

    Looked up as a child of the menu, not through ``action.menu()``: in
    PySide that returns a wrapper already marked as deleted, although the
    submenu is alive (measured - its destroyed signal never fires)."""
    for menu in tray.contextMenu().findChildren(QMenu):
        if menu.title() == title:
            return menu
    return None


# -- the core tray -----------------------------------------------------------

def test_a_module_line_appears_in_the_tooltip(app, subscribe):
    subscribe("tray.tooltip", lambda lines, **_: lines.append("Mikrofon: C920"))
    tray = _tray(app)
    assert "Mikrofon: C920" in tray.toolTip()


def test_the_tooltip_never_runs_past_what_windows_shows(app, subscribe):
    subscribe("tray.tooltip", lambda lines, **_: lines.append("x" * 200))
    tray = _tray(app)
    assert len(tray.toolTip()) <= 127
    assert "x" * 200 not in tray.toolTip(), "cut whole, not mid-word"


def test_a_module_section_becomes_a_submenu(app, subscribe):
    chosen = []
    items = [("Standard", False, lambda: chosen.append("default")),
             ("C920", True, lambda: chosen.append("C920"))]
    subscribe("tray.menu", lambda sections, **_: sections.append(
        {"title": "Mikrofon", "populate": lambda: items}))
    tray = _tray(app)
    sub = _submenu(tray, "Mikrofon")
    assert sub is not None
    actions = sub.actions()
    assert [a.text() for a in actions] == ["Standard", "C920"]
    assert [a.isChecked() for a in actions] == [False, True]
    actions[0].trigger()
    assert chosen == ["default"]


def test_the_submenu_refills_when_opened(app, subscribe):
    """A microphone plugged in after start-up must appear without a restart."""
    devices = [("Standard", True, lambda: None)]
    subscribe("tray.menu", lambda sections, **_: sections.append(
        {"title": "Mikrofon", "populate": lambda: list(devices)}))
    tray = _tray(app)
    devices.append(("USB-Headset", False, lambda: None))
    sub = _submenu(tray, "Mikrofon")
    sub.aboutToShow.emit()
    assert "USB-Headset" in [a.text() for a in sub.actions()]


def test_a_broken_section_does_not_break_the_menu(app, subscribe):
    def boom():
        raise RuntimeError("no audio")
    subscribe("tray.menu", lambda sections, **_: sections.append(
        {"title": "Mikrofon", "populate": boom}))
    tray = _tray(app)
    assert tray.contextMenu() is not None
    assert _submenu(tray, "Mikrofon").actions() == []


def test_a_refresh_request_rebuilds_the_tooltip(app, subscribe):
    name = ["C920"]
    subscribe("tray.tooltip", lambda lines, **_: lines.append(f"Mic: {name[0]}"))
    tray = _tray(app)
    name[0] = "Headset"
    bus.publish("tray.refresh")
    assert "Mic: Headset" in tray.toolTip()


# -- the dictation module's answer -------------------------------------------

@pytest.fixture
def dic(app):
    import module as dic
    before = dic._lang.code
    bus.publish("i18n.language_changed", lang="de")
    yield dic
    bus.publish("i18n.language_changed", lang=before)


def test_the_tooltip_names_the_microphone(dic, monkeypatch):
    m = dic.DictationModule()
    m._settings["input_device"] = "Mikrofon (HD Pro Webcam C920)"
    monkeypatch.setattr(m, "microphone_name",
                        lambda: "Mikrofon (HD Pro Webcam C920)")
    lines = []
    m._on_tray_tooltip(lines=lines)
    assert lines == ["Mikrofon: Mikrofon (HD Pro Webcam C920)"]


def test_the_tooltip_says_when_it_is_the_default(dic, monkeypatch):
    m = dic.DictationModule()
    m._settings["input_device"] = "default"
    monkeypatch.setattr(m, "microphone_name", lambda: "Headset")
    lines = []
    m._on_tray_tooltip(lines=lines)
    assert lines == ["Mikrofon: Headset (Standard)"]


def test_a_long_device_name_is_shortened(dic, monkeypatch):
    m = dic.DictationModule()
    m._settings["input_device"] = "M" * 90
    monkeypatch.setattr(m, "microphone_name", lambda: "M" * 90)
    lines = []
    m._on_tray_tooltip(lines=lines)
    name = lines[0].split(": ", 1)[1]
    assert len(name) == m._TRAY_NAME_MAX and name.endswith("…")


def test_no_microphone_means_no_line(dic, monkeypatch):
    m = dic.DictationModule()
    monkeypatch.setattr(m, "microphone_name", lambda: "")
    lines = []
    m._on_tray_tooltip(lines=lines)
    assert lines == []


def test_the_menu_lists_default_first_and_ticks_the_current_one(dic, monkeypatch):
    m = dic.DictationModule()
    monkeypatch.setattr(dic, "list_input_devices",
                        lambda: [(3, "C920"), (7, "Headset")])
    m._settings["input_device"] = "Headset"
    items = m._tray_microphones()
    assert [label for label, _c, _cb in items] == ["Standardgerät", "C920",
                                                   "Headset"]
    assert [checked for _l, checked, _cb in items] == [False, False, True]


def test_an_old_profile_holding_an_index_is_still_ticked(dic, monkeypatch):
    m = dic.DictationModule()
    monkeypatch.setattr(dic, "list_input_devices",
                        lambda: [(3, "C920"), (7, "Headset")])
    m._settings["input_device"] = 3
    assert [c for _l, c, _cb in m._tray_microphones()] == [False, True, False]


def test_choosing_from_the_tray_switches_and_refreshes(dic, monkeypatch,
                                                      subscribe):
    m = dic.DictationModule()
    monkeypatch.setattr(dic, "list_input_devices",
                        lambda: [(3, "C920"), (7, "Headset")])
    m._settings["input_device"] = "default"
    events = []
    subscribe("tray.refresh", lambda **_: events.append("tray"))
    subscribe("module.settings_changed",
              lambda module_id="", **_: events.append(module_id))

    _label, _checked, choose = m._tray_microphones()[2]
    choose()

    assert m._settings["input_device"] == "Headset", "stored by name"
    assert "tray" in events, "tooltip and menu must show the new microphone"
    assert "dictation" in events, "the profile has to be saved"


def test_choosing_the_same_microphone_again_does_nothing(dic, subscribe):
    m = dic.DictationModule()
    m._settings["input_device"] = "Headset"
    events = []
    subscribe("tray.refresh", lambda **_: events.append("tray"))
    m.set_input_device("Headset")
    assert events == []

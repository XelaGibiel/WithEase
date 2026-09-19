""""Cursor wiederfinden": the first movement after the pointer stood still
shows the highlight - unless the pointer is near the screen centre.

The pointer is simulated; nothing moves the real mouse."""
import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def mouse(app, monkeypatch):
    from withease.modules.mouse import MouseModule
    m = MouseModule()
    m._settings.update({"highlight_enabled": True, "highlight_auto": True,
                        "highlight_auto_delay": 0.3,
                        "highlight_auto_free": 25})
    m._enabled = True
    m.pos = (1800, 100)                       # top right: far from the middle
    monkeypatch.setattr(m, "_cursor_pos", lambda: m.pos)
    monkeypatch.setattr(m, "_monitor_rect", lambda pos: (0, 0, 1920, 1080))
    m.shown = []
    monkeypatch.setattr(m, "_highlight_cursor",
                        lambda: m.shown.append(m.pos))
    stop = threading.Event()
    thread = threading.Thread(target=m._find_loop, args=(stop,), daemon=True)
    thread.start()
    time.sleep(0.1)
    yield m
    stop.set()
    thread.join(1)


def _move(m, x, y, wait=0.15):
    m.pos = (x, y)
    time.sleep(wait)


def test_moving_after_a_pause_shows_the_highlight(mouse):
    time.sleep(0.4)                      # stood still longer than 0.3 s
    _move(mouse, 1750, 120)
    assert mouse.shown == [(1750, 120)]


def test_moving_on_without_a_pause_shows_nothing_more(mouse):
    time.sleep(0.4)
    _move(mouse, 1750, 120)
    for x in (1700, 1650, 1600):         # keeps moving: no new pause
        _move(mouse, x, 120, wait=0.08)
    assert len(mouse.shown) == 1


def test_near_the_centre_nothing_is_shown(mouse):
    _move(mouse, 1000, 560)              # into the middle while moving
    time.sleep(0.4)
    _move(mouse, 1010, 570)
    assert mouse.shown == []


def test_a_shaky_hand_is_no_movement(mouse):
    time.sleep(0.4)
    _move(mouse, 1802, 101)
    assert mouse.shown == []


def test_switched_off_nothing_is_shown(mouse):
    mouse._settings["highlight_auto"] = False
    time.sleep(0.4)
    _move(mouse, 1750, 120)
    assert mouse.shown == []


@pytest.mark.parametrize("pos, free, near", [
    ((960, 540), 25, True),              # the very middle
    ((960 + 260, 540), 25, True),        # 260 px < 25 % of 1080 = 270 px
    ((960 + 280, 540), 25, False),
    ((960, 540), 0, True),               # 0 %: only the exact middle
    ((961, 541), 0, False),
    ((1900, 1000), 50, False),           # a corner is never "the middle"
])
def test_the_free_middle_is_a_share_of_the_screen_height(app, monkeypatch,
                                                           pos, free, near):
    from withease.modules.mouse import MouseModule
    m = MouseModule()
    m._settings["highlight_auto_free"] = free
    monkeypatch.setattr(m, "_monitor_rect", lambda p: (0, 0, 1920, 1080))
    assert m._near_centre(pos) is near


def test_the_settings_show_the_rows_with_the_switch(app):
    from withease.modules.mouse import MouseModule
    m = MouseModule()
    m._settings.update({"highlight_enabled": True})
    page = m.get_settings_widget()
    form = page._highlight_form
    assert not form.isRowVisible(page._highlight_auto_delay)
    assert not form.isRowVisible(page._highlight_auto_free)
    page._highlight_auto_cb.setChecked(True)
    assert m._settings["highlight_auto"] is True
    assert form.isRowVisible(page._highlight_auto_delay)
    assert form.isRowVisible(page._highlight_auto_free)
    page.deleteLater()

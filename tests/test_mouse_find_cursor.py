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
    # delay and free middle live in the box under their switch
    assert page._auto_sub.isAncestorOf(page._highlight_auto_delay)
    assert page._auto_sub.isAncestorOf(page._highlight_auto_free)
    assert not form.isRowVisible(page._auto_sub)
    page._highlight_auto_cb.setChecked(True)
    assert m._settings["highlight_auto"] is True
    assert form.isRowVisible(page._auto_sub)
    page.deleteLater()


def test_every_switch_carries_its_own_box(app):
    from withease.modules.mouse import MouseModule
    m = MouseModule()
    page = m.get_settings_widget()
    form = page._highlight_form
    pairs = [(page._highlight_rings_cb, page._rings_sub),
             (page._highlight_arrow_cb, page._arrow_sub),
             (page._arrow_persistent_cb, page._persist_sub),
             (page._circle_cb, page._circle_sub)]
    for switch, box in pairs:
        for on in (True, False, True):
            switch.setChecked(on)
            if box is page._rings_sub and not on:
                continue        # rings off turns the arrow on - checked below
            assert form.isRowVisible(box) is on, (box.objectName(), on)
    assert page._circle_sub.isAncestorOf(page._circle_radius)
    assert page._persist_sub.isAncestorOf(page._arrow_corner)
    page.deleteLater()


# -- the free middle is drawn while its slider moves --------------------------------

def test_the_free_area_shows_while_sliding_and_goes_after(app, monkeypatch):
    import withease.gui.widgets.free_area_overlay as fao
    monkeypatch.setattr(fao, "HIDE_AFTER_MS", 50)
    from withease.modules.mouse import MouseModule
    m = MouseModule()
    m._settings.update({"highlight_enabled": True, "highlight_auto": True})
    page = m.get_settings_widget()
    slider = page._highlight_auto_free
    assert page._free_overlay is None                 # nothing at start
    slider.slider.setSliderDown(True)                 # grabbed
    slider.setValue(40)
    overlay = page._free_overlay
    assert overlay is not None and overlay.isVisible()
    assert abs(overlay.radius() - overlay.height() * 0.40) < 1
    assert not overlay._hide_timer.isActive()         # held: stays
    slider.slider.setSliderDown(False)                # let go
    page._hide_free_area_soon()
    assert overlay._hide_timer.isActive()
    overlay._hide_timer.setInterval(10)
    overlay._hide_timer.start()
    import time
    deadline = time.monotonic() + 2
    while overlay.isVisible() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not overlay.isVisible()
    page._drop_free_area()
    page.deleteLater()


def test_a_key_press_on_the_slider_hides_by_itself(app):
    from withease.modules.mouse import MouseModule
    m = MouseModule()
    m._settings.update({"highlight_enabled": True, "highlight_auto": True})
    page = m.get_settings_widget()
    page._highlight_auto_free.setValue(30)            # like an arrow key
    assert page._free_overlay._hide_timer.isActive()
    page._drop_free_area()
    assert page._free_overlay is None
    page.deleteLater()


# -- every change to the look shows itself ------------------------------------------

def test_changing_the_look_shows_the_highlight(app, monkeypatch):
    from withease.core.event_bus import bus
    from withease.modules.mouse import MouseModule
    shown = []
    monkeypatch.setattr(bus, "publish",
                        lambda topic, **kw: shown.append((topic, kw))
                        if topic == "mouse.highlight" else None)
    m = MouseModule()
    m._settings.update({"highlight_enabled": True})
    page = m.get_settings_widget()
    assert not hasattr(page, "_highlight_preview_btn")    # no button any more
    page._highlight_radius.setValue(130)
    page._preview_timer.timeout.emit()                   # the short delay
    (topic, kw), = shown
    assert kw["radius"] == 130
    page.deleteLater()


def test_the_sliders_have_about_ten_positions_with_the_defaults(app):
    from withease.modules.mouse import MouseModule
    page = MouseModule().get_settings_widget()
    for slider, default in ((page._highlight_auto_free, 25),
                            (page._highlight_radius, 90),
                            (page._highlight_arrow_thickness, 6),
                            (page._arrow_size, 48),
                            (page._circle_radius, 40),
                            (page._circle_opacity, 25)):
        assert 8 <= slider.slider.maximum() + 1 <= 12
        slider.setValue(default)
        assert slider.value() == default
    page.deleteLater()


# -- one reset button per setting ------------------------------------------------------

def test_each_setting_resets_on_its_own(app):
    from withease.gui.widgets.reset_field import ResetField
    from withease.modules.mouse import MouseModule
    m = MouseModule()
    m._settings.update({"highlight_enabled": True, "highlight_radius": 130,
                        "highlight_duration": 2.4})
    page = m.get_settings_widget()
    page._update_enabled_state(True)      # the module is on: clickable
    assert not hasattr(page, "_highlight_reset_btn")      # no reset-all
    resets = page.findChildren(ResetField)
    assert len(resets) == 16          # 11 in the highlight, 5 elsewhere
    radius = next(r for r in resets if r.field is page._highlight_radius)
    duration = next(r for r in resets if r.field is page._highlight_duration)
    assert not radius.is_default() and not radius.button.isHidden()
    radius.button.click()
    assert page._highlight_radius.value() == 90
    assert m._settings["highlight_radius"] == 90          # saved as usual
    assert radius.button.isHidden()
    assert m._settings["highlight_duration"] == 2.4       # the others stay
    assert "90 px" in radius.button.toolTip()
    page._highlight_radius.setValue(150)                  # changed again
    assert not radius.button.isHidden()
    duration.button.click()
    assert m._settings["highlight_duration"] == 1.6
    page.deleteLater()


def test_the_colour_resets_too(app, monkeypatch):
    from withease.modules.mouse import MouseModule
    m = MouseModule()
    m._settings.update({"highlight_enabled": True,
                        "highlight_color": [10, 20, 30]})
    page = m.get_settings_widget()
    page._update_enabled_state(True)
    assert not page._color_reset.button.isHidden()
    page._color_reset.button.click()
    assert m._settings["highlight_color"] == [255, 140, 0]
    assert page._color_reset.button.isHidden()
    page.deleteLater()

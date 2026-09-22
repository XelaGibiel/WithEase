"""Every module page works like "Cursor hervorheben": topic headings, the
settings that belong to a switch in a box behind it, and a ↺ behind each
setting that puts back just that one."""
import importlib.util
import os
import pathlib

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _load(name, path):
    import sys
    folder = str(path.parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resets(page):
    from withease.gui.widgets.reset_field import ResetField
    return {r.field: r for r in page.findChildren(ResetField)}


def _headings(page):
    from PySide6.QtWidgets import QLabel
    return [w.text() for w in page.findChildren(QLabel)
            if w.objectName() == "settingsGroup"]


def test_keyboard_delay_and_position_reset(app):
    from withease.modules.keyboard import KeyboardModule
    m = KeyboardModule()
    m._settings.update({"delay_ms": 1200})
    page = m.get_settings_widget()
    page._update_enabled_state(True)      # the module is on: clickable
    resets = _resets(page)
    delay = resets[page._delay_ms]
    assert not delay.button.isHidden() and "500 ms" in delay.button.toolTip()
    delay.button.click()
    assert m._settings["delay_ms"] == 500
    assert page._sticky_pos in resets
    assert len(_headings(page)) == 3
    page.deleteLater()


def test_macros_overlay_options_sit_in_a_box(app):
    from withease.modules.macros import MacrosModule
    m = MacrosModule()
    page = m.get_settings_widget()
    page._update_enabled_state(True)
    assert page._ov_sort in _resets(page) and page._ov_pos in _resets(page)
    page._ov_enabled.setChecked(True)
    assert page._ov_sub.isVisibleTo(page)
    page._ov_enabled.setChecked(False)
    assert not page._ov_sub.isVisibleTo(page)
    page.deleteLater()


def test_hydration_delay_only_with_close_after_a_while(app):
    hyd = _load("hydration_module_for_test",
                ROOT / "examples" / "hydration" / "module.py")
    m = hyd.HydrationModule()
    m._settings.update({"interval_minutes": 30, "dismiss_mode": "delay"})
    page = hyd.HydrationSettings(m)
    page._update_enabled_state(True)
    resets = _resets(page)
    interval = resets[page._interval]
    assert not interval.button.isHidden()
    interval.button.click()
    assert m._settings["interval_minutes"] == 60
    assert page._delay_sub.isVisibleTo(page)
    page._dismiss.setCurrentIndex(page._dismiss.findData("instant"))
    assert not page._delay_sub.isVisibleTo(page)
    resets[page._dismiss].button.click()
    assert m._settings["dismiss_mode"] == "delay"
    assert page._delay_sub.isVisibleTo(page)
    assert len(_headings(page)) == 2
    page.deleteLater()


def test_dictation_page_has_headings_and_resets(app):
    dic = _load("module", ROOT / "examples" / "dictation" / "module.py")
    m = dic.DictationModule()
    m._settings.update({"backend": "local", "segment_pause": 3.5,
                        "hallucination_filter": "off"})
    page = m.get_settings_widget()
    page._update_enabled_state(True)
    resets = _resets(page)
    pause = resets[page._segment_pause]
    assert not pause.button.isHidden()
    pause.button.click()
    assert m._settings["segment_pause"] == 2.0
    resets[page._hall_filter].button.click()
    assert m._settings["hallucination_filter"] == "strong"
    for field in (page._mode, page._lang, page._output_mode,
                  page._insert, page._max_seconds, page._history_limit,
                  page._ai_backend):
        assert field in resets
    # the chip size: one ↺ behind the box and its "take over" button
    assert any(page._chip_size.parent() is f for f in resets)
    # the default is named the way the box shows it
    assert page._max_seconds.specialValueText() in \
        resets[page._max_seconds].button.toolTip()
    assert len(_headings(page)) == 6
    # the pause settings are one box behind their switch
    page._segment_cb.setChecked(False)
    assert not page._segment_sub.isVisibleTo(page)
    page.deleteLater()

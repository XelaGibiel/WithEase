"""Deleting must always be reversible.

A confirmation dialog costs a SECOND precise click right after the one that
may already have been a slip – the worst possible moment to ask someone with a
tremor to hit a small button.  So the destructive actions delete straight away
and offer to take it back (src/withease/gui/widgets/undo_bar.py).

That only works if the data is actually recoverable, which is what these tests
pin down: the entries come back, the recordings are moved aside instead of
erased, and the API key is read from the place it is really stored.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from withease.core import config  # noqa: E402
from withease.gui.widgets.undo_bar import UndoBar, show_undo  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Never let a test touch the real profile – config.CONFIG_DIR is resolved
    at import time, so the environment variable is too late here."""
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(config, "APP_CONFIG_FILE", tmp_path / "app.json")
    (tmp_path / "profiles").mkdir(parents=True, exist_ok=True)
    return tmp_path


# -- the bar itself ------------------------------------------------------

def test_undo_button_runs_the_callback_and_closes_the_bar(app):
    window = QWidget()
    window.resize(600, 400)
    window.show()
    undone = []
    bar = show_undo(window, "3 Dinge gelöscht.", lambda: undone.append(True))
    assert bar is not None
    bar._button.click()
    app.processEvents()
    assert undone == [True]
    assert id(window) not in UndoBar._current      # gone after use


def test_expiry_does_not_undo(app):
    """When the offer runs out the deletion stands – the bar must never put
    things back on its own."""
    window = QWidget()
    window.resize(600, 400)
    window.show()
    undone = []
    bar = show_undo(window, "gelöscht", lambda: undone.append(True))
    bar._finish()                                  # what the timeout does
    app.processEvents()
    assert undone == []


def test_hovering_pauses_the_countdown(app):
    """Someone who has reached the bar with the pointer is still deciding."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtCore import QPointF

    window = QWidget()
    window.resize(600, 400)
    window.show()
    bar = show_undo(window, "gelöscht", lambda: None)
    assert bar._timer.isActive()
    bar._holder.eventFilter(bar, QEnterEvent(QPointF(1, 1), QPointF(1, 1),
                                             QPointF(1, 1)))
    assert not bar._timer.isActive()
    bar._holder.eventFilter(bar, QEvent(QEvent.Type.Leave))
    assert bar._timer.isActive()


# -- dictation: history --------------------------------------------------

def test_history_comes_back(app, isolated_config):
    import module as dic
    m = dic.DictationModule()
    m._settings["history"] = ["erster Satz", "zweiter Satz"]
    removed = m.clear_history()
    assert removed == ["erster Satz", "zweiter Satz"]
    assert m.history_count() == 0
    m.restore_history(removed)
    assert m._settings["history"] == ["erster Satz", "zweiter Satz"]


# -- dictation: recordings ----------------------------------------------

def test_recordings_are_moved_aside_not_erased(app, isolated_config):
    import module as dic
    m = dic.DictationModule()
    folder = isolated_config / "dictation_training"
    folder.mkdir()
    (folder / "a.wav").write_bytes(b"x" * 100)
    (folder / "a.txt").write_text("hallo", encoding="utf-8")
    assert m.training_stats()[0] == 1

    aside = m.clear_training_data()
    assert aside and os.path.isdir(aside)          # still on disk …
    assert m.training_stats()[0] == 0              # … but no longer counted
    assert not folder.exists()

    assert m.restore_training_data(aside) is True
    assert m.training_stats()[0] == 1
    assert (folder / "a.txt").read_text(encoding="utf-8") == "hallo"


def test_purge_finally_removes_the_set_aside_folder(app, isolated_config):
    import module as dic
    m = dic.DictationModule()
    folder = isolated_config / "dictation_training"
    folder.mkdir()
    (folder / "a.wav").write_bytes(b"x" * 100)
    aside = m.clear_training_data()
    m.purge_training_data(aside)
    assert not os.path.exists(aside)


# -- dictation: API key --------------------------------------------------

def test_api_key_is_read_and_cleared_where_it_is_really_stored(
        app, isolated_config):
    """Regression: has_api_key()/clear_api_key() used to look at
    _settings["api_key"], which nothing writes – so the data card claimed no
    key was stored while one sat in app.json, and its delete button did
    nothing at all."""
    import module as dic
    m = dic.DictationModule()
    assert m.has_api_key() is False
    m.set_api_key("openrouter", "sk-geheim")
    assert m.has_api_key() is True

    removed = m.clear_api_key()
    assert removed == {"openrouter": "sk-geheim"}
    assert m.has_api_key() is False
    assert m.get_api_key("openrouter") == ""

    m.restore_api_keys(removed)
    assert m.get_api_key("openrouter") == "sk-geheim"
    assert m.has_api_key() is True


def test_dictation_window_history_survives_clearing(app, isolated_config):
    """The window keeps its own list widget, so it needs its own way back –
    including the exact order, which is what makes "Verlauf 2 einfügen" mean
    the same thing before and after."""
    import dictation_window as dw

    handed_back = []
    window = dw.DictationWindow(
        on_history_changed=lambda items: handed_back.append(list(items)))
    window.resize(800, 500)
    window.show()
    for text in ("Erster Satz", "Zweiter Satz"):
        window._add_history_item(text)
    app.processEvents()
    before = [window._history.item(i).text()
              for i in range(window._history.count())]

    window._clear_history()
    app.processEvents()
    assert window._history.count() == 0
    bar = UndoBar._current.get(id(window.window()))
    assert bar is not None, "clearing the history must offer an undo"

    bar._button.click()
    app.processEvents()
    after = [window._history.item(i).text()
             for i in range(window._history.count())]
    assert after == before
    assert handed_back[-1] == ["Erster Satz", "Zweiter Satz"]


# -- macros --------------------------------------------------------------

def test_deleted_macro_can_be_put_back(app, isolated_config):
    from withease.modules.macros import Macro, MacrosModule
    from withease.gui.settings.macros_settings import MacrosSettingsWidget

    module = MacrosModule()
    module._macros = [
        Macro(id="a", label="Erstes", trigger_key="'a'", type="text",
              payload={"text": "A"}),
        Macro(id="b", label="Zweites", trigger_key="'b'", type="text",
              payload={"text": "B"}),
    ]
    page = MacrosSettingsWidget(module)
    page.resize(900, 700)
    page.show()
    app.processEvents()

    page._table.selectRow(0)
    page._on_delete()
    app.processEvents()
    assert [m.id for m in module._macros] == ["b"]

    bar = UndoBar._current.get(id(page.window()))
    assert bar is not None, "deleting a macro must offer an undo"
    bar._button.click()
    app.processEvents()
    assert [m.id for m in module._macros] == ["a", "b"]     # and in order


# -- one list, not two ---------------------------------------------------

def test_text_blocks_move_into_the_macros(app, isolated_config):
    """A named piece of text is the same thing whether it is fired by a key or
    spoken, so it must only exist ONCE.  The dictation add-on used to keep its
    own second list which did not show the macros – and the macro list did not
    show the snippets."""
    from withease.core.event_bus import bus
    from withease.modules.macros import MacrosModule
    import module as dic

    macros = MacrosModule()
    macros._macros = []
    macros.start()
    try:
        m = dic.DictationModule()
        m._settings["snippets"] = [{"name": "Grußformel",
                                    "prompt": "Viele Grüße"}]
        page = dic.DictationSettingsWidget(m)
        page._move_snippets_to_macros()

        assert [(x.label, x.type, x.trigger_key) for x in macros._macros] == [
            ("Grußformel", "text", "")]        # no key taken in macro mode
        assert m.snippets_raw() == []          # the second list is gone
        assert m.lookup_snippet("grußformel")[0] == "Viele Grüße"

        out: list = []
        bus.publish("macros.add_text_block", name="Grußformel", text="x",
                    out=out)
        assert out == [] and len(macros._macros) == 1   # no duplicate
    finally:
        macros.stop()

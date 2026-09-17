"""Managing the speech models: how big, how far, and how to get rid of them.

Taken from OpenWhispr's model picker, which shows a size per model, a real
progress bar while downloading and a delete button.  WithEase showed a bare
name in a dropdown, no progress at all, and had no way whatsoever to remove a
downloaded model - large-v3 alone sits in the cache with about three
gigabytes, and "Deine Daten" did not even mention it.
"""
import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from withease.core.event_bus import bus  # noqa: E402

import module as dic  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """A Hugging Face cache of our own, with two models in it."""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "nowhere"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "nowhere-either"))
    for model, size in (("large-v3", 3000), ("base", 500)):
        snap = tmp_path / f"models--Systran--faster-whisper-{model}" / "snapshots" / "a1"
        snap.mkdir(parents=True)
        (snap / "model.bin").write_bytes(b"x" * size)
    return tmp_path


# -- how big is it -----------------------------------------------------------

def test_the_dropdown_names_the_size(app):
    """Choosing "large-v3" means a three-gigabyte download."""
    m = dic.DictationModule()
    page = m.get_settings_widget()
    box = page._local_model
    labels = [box.itemText(i) for i in range(box.count())]
    assert any("large-v3" in text and "GB" in text for text in labels)
    # …and the stored value is still the bare model name.
    assert [box.itemData(i) for i in range(box.count())] == dic.LOCAL_MODELS
    page.deleteLater()


def test_what_is_on_disk_is_measured(cache):
    assert dic.model_bytes_on_disk("large-v3") == 3000
    assert dic.model_bytes_on_disk() == 3500
    assert len(dic.model_folders()) == 2


def test_nothing_on_disk_is_no_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "a"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "b"))
    assert dic.model_bytes_on_disk() == 0
    assert dic.model_folders() == []


# -- how far along is it -----------------------------------------------------

def test_the_chip_counts_the_download_up(app, cache, monkeypatch):
    """The percentage comes from the files, so it also works in the .exe,
    where another process does the downloading."""
    m = dic.DictationModule()
    seen = []

    def listen(state="", detail="", **_):
        if state == "loading":
            seen.append(detail)

    bus.subscribe("dictation.state", listen)
    try:
        monkeypatch.setattr(dic, "model_is_downloaded", lambda _n: False)
        monkeypatch.setattr(dic, "_MODEL_MB", {"large-v3": 3090})
        monkeypatch.setattr(dic, "model_bytes_on_disk",
                            lambda _n="*": 1_545_000_000)   # half of it
        m._state = "transcribing"
        with m._model_phase("large-v3"):
            for _ in range(40):
                if any("%" in text for text in seen):
                    break
                time.sleep(0.05)
    finally:
        bus.unsubscribe("dictation.state", listen)

    with_percent = [text for text in seen if "%" in text]
    assert with_percent, "a download has to show how far it has got"
    assert "50 %" in with_percent[0]


def test_the_percentage_stops_when_the_download_does(app, monkeypatch):
    m = dic.DictationModule()
    monkeypatch.setattr(dic, "model_is_downloaded", lambda _n: False)
    monkeypatch.setattr(dic, "_MODEL_MB", {"base": 145})
    monkeypatch.setattr(dic, "model_bytes_on_disk", lambda _n="*": 1000)
    with m._model_phase("base"):
        pass
    before = len(dic.threading.enumerate())
    time.sleep(1.0)
    assert len(dic.threading.enumerate()) <= before, "the watcher must end"


# -- getting rid of it -------------------------------------------------------

def test_models_are_moved_aside_not_erased(app, cache):
    m = dic.DictationModule()
    assert m.model_stats() == (2, 3500)

    moved = m.clear_models()
    assert len(moved) == 2
    assert all(os.path.isdir(path) for path in moved), "still on disk …"
    assert m.model_stats() == (0, 0), "… but no longer counted"

    assert m.restore_models(moved) is True
    assert m.model_stats() == (2, 3500)


def test_purging_finally_removes_them(app, cache):
    m = dic.DictationModule()
    moved = m.clear_models()
    m.purge_models(moved)
    assert not any(os.path.exists(path) for path in moved)
    assert m.model_stats() == (0, 0)


def test_deleting_lets_go_of_the_loaded_model(app, cache):
    """Windows refuses to rename a folder whose files are still open."""
    m = dic.DictationModule()
    m._local_model = object()
    m._local_model_name = "large-v3"
    m.clear_models()
    assert m._local_model is None and m._local_model_name is None


def test_the_data_page_shows_the_models(app, cache):
    m = dic.DictationModule()
    page = m.get_settings_widget()
    text = page._data_models.text()
    assert "2" in text and ("KB" in text or "B" in text)
    page.deleteLater()


# -- the dots in the dropdown ------------------------------------------------

def _dot_colour(box, name):
    index = next(i for i in range(box.count()) if box.itemData(i) == name)
    icon = box.itemIcon(index)
    if icon.isNull():
        return None
    return icon.pixmap(12, 12).toImage().pixelColor(6, 6).name().lower()


def test_the_dropdown_says_which_model_is_loaded(app, monkeypatch):
    """Loaded or merely downloaded decides whether the next dictation starts
    at once or waits a quarter of a minute."""
    monkeypatch.setattr(dic, "model_is_downloaded",
                        lambda name: name in ("medium", "large-v3"))
    m = dic.DictationModule()
    m._local_model = object()
    m._local_model_name = "medium"

    page = m.get_settings_widget()
    page._refresh_model_dots()
    box = page._local_model
    green, grey = dic._state_colors()

    assert _dot_colour(box, "medium") == green.lower(), "loaded is green"
    assert _dot_colour(box, "large-v3") == grey.lower(), "on disk is grey"
    assert _dot_colour(box, "tiny") is None, "nothing there, nothing shown"
    page.deleteLater()


def test_every_entry_explains_its_dot(app, monkeypatch):
    monkeypatch.setattr(dic, "model_is_downloaded", lambda name: name == "base")
    m = dic.DictationModule()
    page = m.get_settings_widget()
    page._refresh_model_dots()
    box = page._local_model
    tips = [box.itemData(i, 3) for i in range(box.count())]
    assert all(tip for tip in tips), "a dot without a word is a riddle"
    page.deleteLater()


def test_the_loaded_model_is_reported_from_wherever_it_sits(app):
    """This process, the packaged app's worker, or the whisper.cpp server."""
    m = dic.DictationModule()
    assert m.loaded_model() == ""

    m._local_model, m._local_model_name = object(), "medium"
    assert m.loaded_model() == "medium"

    m._local_model, m._local_model_name = None, None
    m._whisper_proc._running_model = "large-v3"
    assert m.loaded_model() == "large-v3"

    m._whisper_proc._running_model = None

    class _Server:
        def alive(self):
            return True

        def model(self):
            return "C:/x/ggml-large-v3-turbo-q5_0.bin"

    m._whispercpp = _Server()
    assert m.loaded_model() == "large-v3-turbo-q5_0"


# -- the load button knows the state -----------------------------------------

def test_the_load_button_is_off_for_a_model_that_is_loaded(app, monkeypatch):
    monkeypatch.setattr(dic, "model_is_downloaded", lambda _n: True)
    m = dic.DictationModule()
    m._local_model, m._local_model_name = object(), "medium"
    m._settings["local_model"] = "medium"

    page = m.get_settings_widget()
    # The whole card is greyed out while the module is off, which would make
    # this test pass for the wrong reason.
    page._update_enabled_state(True)
    page._refresh_model_dots()
    assert not page._model_load_btn.isEnabled()
    assert page._model_load_btn.toolTip(), "say why it cannot be pressed"

    page._local_model.setCurrentIndex(
        next(i for i in range(page._local_model.count())
             if page._local_model.itemData(i) == "large-v3"))
    assert page._model_load_btn.isEnabled(), "another model can be loaded"
    page.deleteLater()


# -- the action inside the list ----------------------------------------------

def test_what_each_entry_offers(app, monkeypatch):
    monkeypatch.setattr(dic, "model_is_downloaded",
                        lambda name: name in ("medium", "large-v3"))
    m = dic.DictationModule()
    m._local_model, m._local_model_name = object(), "medium"
    page = m.get_settings_widget()

    assert page._model_action_for("medium")[0] == "unload"
    assert page._model_action_for("large-v3")[0] == "delete"
    assert page._model_action_for("tiny")[0] == "", "nothing to do here"
    page.deleteLater()


def test_only_entries_with_an_action_get_a_button(app, monkeypatch):
    from PySide6.QtCore import QRect
    monkeypatch.setattr(dic, "model_is_downloaded", lambda name: name == "base")
    m = dic.DictationModule()
    page = m.get_settings_widget()
    box = page._local_model
    delegate = box.itemDelegate()
    row = QRect(0, 0, 400, 44)

    def rect_for(name):
        index = next(i for i in range(box.count()) if box.itemData(i) == name)
        return delegate.action_rect(row, box.model().index(index, 0))

    assert rect_for("base") is not None
    assert rect_for("tiny") is None
    assert rect_for("base").right() <= row.right(), "stays inside the row"
    page.deleteLater()


def test_pressing_unload_lets_go_of_the_model(app, monkeypatch):
    monkeypatch.setattr(dic, "model_is_downloaded", lambda _n: True)
    m = dic.DictationModule()
    m._local_model, m._local_model_name = object(), "medium"
    page = m.get_settings_widget()

    page._run_model_action("medium", "local")

    assert m.loaded_model() == ""
    assert page._model_status.text(), "say what happened"
    page.deleteLater()


def test_pressing_delete_moves_that_one_model_aside(app, cache, monkeypatch):
    """And only that one - the other stays."""
    offered = {}
    monkeypatch.setattr(dic, "_show_undo",
                        lambda widget, text, undo: offered.update(
                            text=text, undo=undo) or True)
    m = dic.DictationModule()
    page = m.get_settings_widget()
    assert m.model_stats()[0] == 2

    page._run_model_action("large-v3", "local")

    assert m.model_stats()[0] == 1, "the other model is untouched"
    assert "large-v3" in offered["text"], "and it can be undone"

    offered["undo"]()
    assert m.model_stats()[0] == 2
    page.deleteLater()


def test_unloading_lets_go_of_all_three_places(app):
    m = dic.DictationModule()
    m._local_model, m._local_model_name = object(), "medium"
    m._whisper_proc._running_model = "medium"

    class _Server:
        alive_called = False

        def alive(self):
            return True

        def model(self):
            return "ggml-base.bin"

        def stop(self):
            _Server.alive_called = True

    m._whispercpp = _Server()

    assert m.unload_model() is True
    assert m._local_model is None
    assert _Server.alive_called, "the whisper.cpp server too"


def test_the_button_does_not_crowd_the_model_name(app, monkeypatch):
    """Reported from the screenshot: at 12 px the name and the button read as
    one lump."""
    from PySide6.QtGui import QFontMetrics

    monkeypatch.setattr(dic, "model_is_downloaded", lambda name: name == "base")
    m = dic.DictationModule()
    page = m.get_settings_widget()
    box = page._local_model
    delegate = box.itemDelegate()
    index = next(box.model().index(i, 0) for i in range(box.count())
                 if box.itemData(i) == "base")

    metrics = QFontMetrics(box.font())
    name_width = metrics.horizontalAdvance(box.itemText(index.row()))
    button_width = (metrics.horizontalAdvance(dic._t("model.delete"))
                    + 2 * delegate._PAD)

    # The open list is as wide as the box unless a minimum says otherwise -
    # so that minimum is what has to hold the room, not the delegate's hint.
    room = box.view().minimumWidth() - name_width - button_width
    assert room >= 24, f"only {room} px between the name and the button"
    page.deleteLater()


def test_the_room_grows_with_the_font(app):
    from PySide6.QtGui import QFont

    m = dic.DictationModule()
    page = m.get_settings_widget()
    box = page._local_model
    delegate = box.itemDelegate()

    small = delegate._gap()
    # The BOX's font is the one that counts: the popup view keeps the font it
    # was built with and would report a stale size.
    box.setFont(QFont(box.font().family(), 16))
    assert delegate._gap() > small, "at 16 pt the list must stay as airy"
    page.deleteLater()

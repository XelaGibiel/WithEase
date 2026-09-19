""""Fehler merken": the last parts are kept in memory and saved on request -
with their audio, what was recognised and what was inserted."""
import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _Win:
    def __init__(self):
        self.done = []

    def report_done(self, message, saved):
        self.done.append((message, saved))


@pytest.fixture
def module(app, monkeypatch):
    import threading

    import module as dic
    m = dic.DictationModule()
    m._settings.update({"backend": "local", "output_mode": "window"})
    m._window = _Win()
    m._active_mode = "auto"
    monkeypatch.setattr(m, "on_settings_changed", lambda: None)
    # write at once instead of on a worker thread
    monkeypatch.setattr(threading, "Thread",
                        lambda target, **_k: type(
                            "T", (), {"start": lambda self: target()})())
    return m


def _part(m, raw, text):
    m._remember_part(b"RIFF-fake-" + raw.encode(), raw)
    m._finish_part(text)


def test_only_the_last_five_parts_stay_in_memory(module):
    for i in range(7):
        _part(module, f"roh {i}", f"Text {i}.")
    assert [p["raw"] for p in module._recent_parts] == [
        f"roh {i}" for i in range(2, 7)]


def test_the_parts_are_saved_with_audio_and_both_texts(module, tmp_path):
    module._settings["report_dir"] = str(tmp_path)
    _part(module, "Ich habe einen Fehler Punkt", "Ich habe einen Fehler.")
    _part(module, "Fehler merken.", "Fehler merken.")     # the command itself
    module.report_error("Ich habe einen Fehler.")
    (report,) = list(tmp_path.iterdir())
    names = sorted(os.listdir(report))
    assert names == ["bericht.json", "bericht.txt", "teil-1.wav"]
    assert (report / "teil-1.wav").read_bytes().startswith(b"RIFF-fake-")
    data = json.loads((report / "bericht.json").read_text(encoding="utf-8"))
    assert data["parts"][0]["raw"] == "Ich habe einen Fehler Punkt"
    assert data["parts"][0]["text"] == "Ich habe einen Fehler."
    assert data["window_text"] == "Ich habe einen Fehler."
    assert "local_model" in data["settings"]
    text = (report / "bericht.txt").read_text(encoding="utf-8")
    assert "erkannt:    Ich habe einen Fehler Punkt" in text
    (message, saved), = module._window.done
    assert saved and str(tmp_path) in message


def test_nothing_dictated_yet_says_so(module, tmp_path):
    module._settings["report_dir"] = str(tmp_path)
    module.report_error("")
    assert module._window.done == [(module._window.done[0][0], False)]
    assert list(tmp_path.iterdir()) == []


def test_the_folder_is_asked_for_once(module, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(module, "choose_report_dir",
                        lambda parent=None: asked.append(1) or "")
    _part(module, "Hallo", "Hallo.")
    module.report_error("Hallo.")
    assert asked == [1] and module._window.done[-1][1] is False   # cancelled


def test_saying_it_or_clicking_it_reaches_the_module(app):
    import dictation_window as dw
    calls = []
    win = dw.DictationWindow(on_insert=lambda text: True,
                             on_copy=lambda text: None,
                             on_report_error=calls.append)
    win._edit.setPlainText("Ein Satz.")
    win._on_transcript("Fehler merken.", "auto", [])
    win._report_btn.click()
    assert calls == ["Ein Satz.", "Ein Satz."]
    assert win.text() == "Ein Satz."                 # not written as text
    win._apply_report_done("gespeichert", True)
    assert win._report_btn.text().startswith("✓")
    win.deleteLater()           # the pending reset must not outlive it


def test_without_the_callback_there_is_no_button(app):
    import dictation_window as dw
    win = dw.DictationWindow(on_insert=lambda text: True,
                             on_copy=lambda text: None)
    assert not win._report_btn.isVisibleTo(win)
    win.deleteLater()


def test_the_settings_show_where_errors_go(app, module, tmp_path):
    page = module.get_settings_widget()
    assert not page._report_open.isEnabled()          # no folder yet
    module._settings["report_dir"] = str(tmp_path)
    page._show_report_dir()
    assert page._report_dir.text() == str(tmp_path)
    assert page._report_open.isEnabled()
    page.deleteLater()


def test_the_compact_view_shows_only_the_flag(app):
    import dictation_window as dw
    win = dw.DictationWindow(on_insert=lambda text: True,
                             on_copy=lambda text: None,
                             on_report_error=lambda text: None, compact=True)
    assert win._report_btn.text() == "⚑"
    win.set_compact(False)
    assert win._report_btn.text().startswith("⚑ ")
    win.deleteLater()

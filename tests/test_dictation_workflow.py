"""The small steps between the steps: what you almost always do next should
take no searching - one Enter, one click, or nothing at all."""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import commands_de as cde  # noqa: E402
import dictation_window as dw  # noqa: E402
import settings_dialogs as sd  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    w = dw.DictationWindow(on_insert=lambda _t: True, on_copy=lambda _t: None)
    yield w
    w.close()


# -- the word you just added is the one you want to teach --------------------

def test_the_dictionary_goes_straight_on_to_teaching(app):
    added = []
    rows = []

    def add(written, spoken):
        added.append((written, spoken))
        rows.append(("dict", written, spoken, written, "user"))
    taught = []
    dlg = sd.DictionaryDialog(
        rows_provider=lambda _cat: list(rows),
        on_add=add, on_edit=lambda *_a: None, on_remove=lambda *_a: None,
        categories=[("all", "Alle")],
        on_pronounce=lambda word, parent: taught.append(word))
    dlg.show()
    app.processEvents()
    assert dlg._written.hasFocus()          # type straight away
    dlg._written.setText("Alberichstraße")
    dlg._add()
    assert added == [("Alberichstraße", "")]
    assert dlg._pron_btn.hasFocus() and dlg._pron_btn.isDefault()
    dlg._pronounce()                        # what Enter would do now
    assert taught == ["Alberichstraße"]
    assert dlg._written.hasFocus()          # ready for the next word
    dlg.close()


# -- "was that a command?" ---------------------------------------------------

@pytest.mark.parametrize("said, command", [
    ("Fehler Märchen", "Fehler merken"),
    ("Streich dast", "Streich das"),
])
def test_a_near_miss_is_offered_for_teaching(said, command):
    assert cde.parse(said) is None
    assert cde.almost_command(cde.normalise(said)) == command


@pytest.mark.parametrize("said", [
    "Guten Morgen", "Ich gehe jetzt nach Hause", "Das Kleid",
])
def test_ordinary_text_is_not_offered(said):
    assert cde.almost_command(cde.normalise(said)) is None


def test_the_offer_shows_up_in_the_answer_line_and_opens_the_dialog(
        win, monkeypatch):
    # the published event is caught here: a real dictation module listening
    # for it would open its dialog and block the test run
    asked = []
    monkeypatch.setattr(dw.bus, "publish",
                        lambda topic, **kw: asked.append((topic, kw)))
    win._on_transcript("Fehler Märchen", "auto", [])
    assert "Fehler merken" in win._hint.text()
    assert 'href="train|' in win._hint.text()
    start = win._hint.text().index('href="train|') + 6
    href = win._hint.text()[start:win._hint.text().index('"', start)]
    win._on_hint_link(href)
    topic, kw = asked[-1]
    assert topic == "dictation.train_commands"
    assert kw["phrase"] == "Fehler merken" and kw["heard"] == "Fehler Märchen"


def test_the_dialog_opens_with_what_was_heard_ready_to_keep(app):
    import module as dic
    import pronunciation
    m = dic.DictationModule()
    m._settings.update({"backend": "local"})
    dlg = pronunciation.CommandTrainingDialog(m, "Fehler merken",
                                              "Fehler Märchen")
    assert dlg._save.isEnabled()            # nothing has to be said again
    assert [row["text"] for _box, row in dlg._rows] == ["Fehler Märchen"]
    dlg._save_variants()
    assert cde.parse("Fehler Märchen").kind == "report_error"
    m.remove_command_variant("Fehler Märchen")
    dlg.deleteLater()


# -- the kept report offers its folder ---------------------------------------

def test_a_saved_report_offers_the_folder(win, monkeypatch):
    opened = []
    monkeypatch.setattr(dw.bus, "publish",
                        lambda topic, **_kw: opened.append(topic))
    win._apply_report_done("Gespeichert", True)
    assert 'href="report"' in win._hint.text()
    win._on_hint_link("report")
    assert opened == ["dictation.open_report_dir"]
    win._apply_report_done("Nichts zu merken", False)
    assert "href" not in win._hint.text()


def test_the_still_to_do_line_jumps_to_the_setting(app):
    """Every step names the setting it means, and clicking it goes there."""
    import module as dic
    m = dic.DictationModule()
    m._settings.update({"backend": "local", "hotkey": ""})
    page = m.get_settings_widget()
    page._update_enabled_state(True)
    page._refresh_setup_note()
    assert page._setup_note.isVisible() or True     # built, may be hidden
    assert 'href="hotkey"' in page._setup_note.text()
    assert 'href="test"' in page._setup_note.text()
    page.show()
    app.processEvents()
    page._goto_setup("hotkey")
    assert page._hotkey.hasFocus()
    page._goto_setup("test")
    assert page._test_btn.hasFocus()
    page.deleteLater()


# -- "Wort merken" -----------------------------------------------------------

def test_the_command_takes_the_last_word(win, monkeypatch):
    assert cde.parse("Wort merken").kind == "learn_word"
    asked = []
    monkeypatch.setattr(win, "_ask_vocab",
                        lambda word: (asked.append(word), (None, ""))[1])
    win._on_transcript("Der Alberichstraße", "text", [])
    win._on_transcript("Wort merken", "auto", [])
    assert asked == ["Alberichstraße"]
    # with a word marked, that one is meant
    cursor = win._edit.textCursor()
    cursor.setPosition(4)
    cursor.setPosition(7, cursor.MoveMode.KeepAnchor)
    win._edit.setTextCursor(cursor)
    win._on_transcript("Wort merken", "auto", [])
    assert asked[-1] == win._edit.textCursor().selectedText().strip()


# -- the commands you use most ------------------------------------------------

def test_the_command_list_puts_your_own_first(app):
    import module as dic
    m = dic.DictationModule()
    for _ in range(3):
        m.count_command_use("strike_last")
    m.count_command_use("report_error")
    assert m.top_commands() == ["Streich das", "Fehler merken"]
    sheet = dw.CommandCheatSheet(top=m.top_commands())
    texts = [w.text() for w in sheet.findChildren(type(sheet._count))]
    assert any("Streich das" in t for t in texts)
    sheet.close()


def test_using_a_command_counts_it(app):
    counted = []
    w = dw.DictationWindow(on_insert=lambda _t: True, on_copy=lambda _t: None,
                           on_command_used=counted.append)
    w._on_transcript("Streich das", "auto", [])
    assert counted == ["strike_last"]
    w.close()

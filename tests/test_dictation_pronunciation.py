"""Aussprache anlernen: say a dictionary word, keep how it was misheard.

Parakeet cannot be told the user's words before it listens, and matching by
similarity has to be careful.  Saying the word a few times shows exactly how
the recogniser writes it - those spellings are then replaced for certain.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import pronunciation as pr  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _german():
    from withease.core.event_bus import bus
    import dict_i18n
    before = dict_i18n._lang.code
    bus.publish("i18n.language_changed", lang="de")
    yield
    bus.publish("i18n.language_changed", lang=before)


@pytest.fixture
def module(app):
    import module as dic
    m = dic.DictationModule()
    m._settings["dictionary"] = []
    return m


# -- what was heard ----------------------------------------------------------------

def test_the_takes_are_grouped_by_spelling():
    rows = pr.evaluate("Parakeet", [
        ("Parakeet", "Parakit."), ("Whisper", "Para Kid"),
        ("Parakeet", "parakit"), ("Whisper", "Parakeet."),
    ])
    wrong = [r for r in rows if not r["correct"]]
    assert [r["text"] for r in wrong] == ["Parakit", "Para Kid"]
    assert wrong[0]["count"] == 2 and wrong[0]["engines"] == ["Parakeet"]
    assert any(r["correct"] for r in rows), "the right spelling is shown too"


@pytest.mark.parametrize("variant, is_risky", [
    ("Parakit", False),
    ("Para Kid", False),
    ("viel", True),            # an ordinary word: every "viel" would change
    ("die Rechnung", True),
    ("ab", True),
])
def test_ordinary_words_are_not_ticked(variant, is_risky):
    assert pr.risky(variant) is is_risky


def test_nothing_heard_gives_no_rows():
    assert pr.evaluate("Parakeet", [("Parakeet", ""), ("Whisper", " . ")]) == []


# -- where it is kept ----------------------------------------------------------------

def test_variants_are_kept_on_the_entry_and_used(module):
    module.add_dictionary_entry("Parakeet", "", "user")
    module.add_heard_variants("Parakeet", ["Parakit", "Para Kid", "parakit",
                                           "Parakeet", ""])
    entry = module._dictionary()[0]
    assert entry["v"] == ["Parakit", "Para Kid"]
    assert ("Parakit", "Parakeet") in module.spoken_forms()

    from vocabulary import apply_spoken_forms
    assert apply_spoken_forms("Ich teste Para Kid heute.",
                              module.spoken_forms()) == \
        "Ich teste Parakeet heute."


def test_a_new_word_is_added_with_its_variants(module):
    module.add_heard_variants("OpenWhispr", ["Open Whisper"])
    assert module._dictionary() == [{"w": "OpenWhispr", "s": "", "src": "user",
                                     "v": ["Open Whisper"]}]


def test_the_spoken_form_is_not_repeated_as_a_variant(module):
    module.add_dictionary_entry("MediaMarkt", "Media Markt", "user")
    module.add_heard_variants("MediaMarkt", ["media markt", "Mediamark"])
    assert module._dictionary()[0]["v"] == ["Mediamark"]


def test_the_list_shows_how_many_were_taught(module):
    module.add_heard_variants("Parakeet", ["Parakit", "Para Kid"])
    rows = module.dictionary_rows("all")
    assert rows[0][4].endswith("🎤 2")
    assert module.dictionary_rows("spoken"), "counts as having spoken forms"


def test_export_and_import_keep_the_variants(module, tmp_path):
    module.add_dictionary_entry("Parakeet", "Para Kiet", "user")
    module.add_heard_variants("Parakeet", ["Parakit"])
    path = tmp_path / "woerterbuch.txt"
    module.export_dictionary(str(path))
    assert "Parakit = Parakeet" in path.read_text(encoding="utf-8")

    module._settings["dictionary"] = []
    module.import_dictionary(str(path))
    entry = module._dictionary()[0]
    assert entry["s"] == "Para Kiet" and entry["v"] == ["Parakit"]


def test_an_entry_without_variants_is_saved_as_before(module):
    module.add_dictionary_entry("Rechnung", "", "user")
    assert module._settings["dictionary"] == [
        {"w": "Rechnung", "s": "", "src": "user"}]


# -- the dialog ------------------------------------------------------------------------

class _FakeModule:
    """Three takes arrive as soon as listening starts."""

    def __init__(self, heard):
        self.heard = list(heard)
        self.saved = []

    def dictated_texts(self):
        return []

    def pronunciation_engines(self):
        return [("Parakeet", lambda pcm: self.heard.pop(0))]

    def capture_takes(self, on_take, on_level=None):
        for _ in range(3):
            on_take(b"\x00\x00" * 1600)

        class _Stop:
            def stop(self):
                pass
        return _Stop()

    def add_heard_variants(self, word, variants):
        self.saved.append((word, variants))


def _wait(app, condition, seconds=5.0):
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end and not condition():
        app.processEvents()
        time.sleep(0.01)


def test_the_dialog_records_evaluates_and_saves(app):
    fake = _FakeModule(["Parakit.", "Parakit", "Parakeet"])
    dlg = pr.PronunciationDialog("Parakeet", fake)
    dlg._begin()
    _wait(app, lambda: dlg._save.isEnabled())
    assert dlg.chosen() == ["Parakit"]
    dlg._save_variants()
    assert fake.saved == [("Parakeet", ["Parakit"])]
    dlg.close()


def test_an_ordinary_word_is_offered_but_not_ticked(app):
    fake = _FakeModule(["viel", "viel", "viel"])
    dlg = pr.PronunciationDialog("Kiel", fake)
    dlg._begin()
    _wait(app, lambda: dlg._save.isEnabled())
    assert dlg.chosen() == []
    boxes = [box for box, _row in dlg._rows if box is not None]
    assert boxes and not boxes[0].isChecked()
    dlg.close()


def test_all_correct_means_nothing_to_save(app):
    fake = _FakeModule(["Parakeet", "Parakeet.", "parakeet"])
    dlg = pr.PronunciationDialog("Parakeet", fake)
    dlg._begin()
    _wait(app, lambda: dlg._start.isEnabled())
    assert not dlg._save.isEnabled()
    assert "richtig" in dlg._status.text()
    dlg.close()


def test_no_recogniser_says_so(app):
    fake = _FakeModule([])
    fake.pronunciation_engines = lambda: []
    dlg = pr.PronunciationDialog("Parakeet", fake)
    dlg._begin()
    _wait(app, lambda: dlg._start.isEnabled())
    assert "Erkenner" in dlg._status.text()
    dlg.close()


def test_the_dictionary_dialog_offers_it_for_a_typed_word(app, module):
    from settings_dialogs import DictionaryDialog
    asked = []
    dlg = DictionaryDialog(
        rows_provider=module.dictionary_rows,
        on_add=lambda w, s: module.add_dictionary_entry(w, s, "user"),
        on_edit=module.dictionary_edit, on_remove=module.dictionary_remove,
        categories=[("all", "Alle")],
        on_pronounce=lambda word, parent: asked.append(word))
    dlg._written.setText("Parakeet")
    dlg._pronounce()
    assert asked == ["Parakeet"]
    assert module.glossary_words() == ["Parakeet"], "a new word is added first"

    dlg._table.setCurrentCell(0, 0)
    dlg._pronounce()
    assert asked == ["Parakeet", "Parakeet"], "or the selected entry"
    dlg.close()


def test_a_word_from_your_own_dictations_is_not_ticked():
    own = ["Ich habe den Para Kit gestern bestellt."]
    assert pr.risky("Para Kit", own) is True
    assert pr.risky("Parakit", own) is False

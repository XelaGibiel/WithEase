"""Commands Whisper writes a little differently still work: near matches
("Streicht das") and spellings taught with "Befehl einlernen"."""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

import commands_de as cde  # noqa: E402


@pytest.fixture(autouse=True)
def no_training():
    cde.set_trained({})
    yield
    cde.set_trained({})


@pytest.mark.parametrize("said, kind", [
    ("Streicht das.", "strike_last"),
    ("Streich dass!", "strike_last"),
    ("Streiche des", "strike_last"),
    ("Fehler merkten.", "report_error"),
    ("Text übernehme", "insert"),
    ("Neue Zeilen", "newline"),
    ("Nächste Satz", "next_sentence"),
])
def test_a_command_written_slightly_differently_still_counts(said, kind):
    cmd = cde.parse(said)
    assert cmd is not None and cmd.kind == kind


@pytest.mark.parametrize("said", [
    "Das Kleid.",         # near "das klein" - too short a phrase to guess
    "Doch mal.",          # another first letter is another word
    "Viel merken.",
    "Lies das.",
    "Nächster Schritt",
    "Streicht das Wort aus dem Text.",    # longer: dictation
    "Streicher",          # one word is never guessed ("streichen" is one)
])
def test_normal_text_stays_text(said):
    assert cde.parse(said) is None


def test_a_taught_spelling_counts_as_its_command():
    assert cde.parse("Frisch das") is None
    cde.set_trained({"Frisch das.": "Streich das"})
    assert cde.parse("Frisch, das!").kind == "strike_last"
    # also as one word while dictating, where single words are ignored
    cde.set_trained({"Streichdas": "streich das"})
    assert cde.command_in_dictation("Streichdas.").kind == "strike_last"


def test_the_list_to_teach_has_fixed_commands_only():
    names = cde.trainable_commands()
    assert "Streich das" in names and "Fehler merken" in names
    assert not any(ch.isdigit() for name in names for ch in name)
    assert not any(" A " in f" {name} " for name in names)


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _real_module():
    import module as dic
    m = dic.DictationModule()
    m._settings.update({"backend": "local"})
    return m


def test_the_dialog_keeps_what_was_heard(app):
    import pronunciation
    m = _real_module()
    dlg = pronunciation.CommandTrainingDialog(m, "Streich das")
    heard = [("Whisper", "Frisch das."), ("Whisper", "Streich das."),
             ("Whisper", "Kopieren.")]
    dlg._show_results(heard)
    rows = {row["text"]: (box, row) for box, row in dlg._rows}
    assert rows["Streich das"][1]["correct"]            # already works
    assert rows["Kopieren"][1].get("note")              # another command
    box = rows["Frisch das"][0]
    assert box is not None and box.isChecked()
    dlg._save_variants()
    assert m.command_variants("Streich das") == ["frisch das"]
    assert cde.parse("Frisch das").kind == "strike_last"
    assert "frisch das" in dlg._known.text()
    dlg._forget("frisch das")
    assert m.command_variants("Streich das") == []
    assert cde.parse("Frisch das") is None
    dlg.deleteLater()


def test_the_dialog_switches_between_commands(app):
    import pronunciation
    m = _real_module()
    m.add_command_variants("Fehler merken", ["Fehler Märchen"])
    dlg = pronunciation.CommandTrainingDialog(m)
    dlg._pick.setCurrentIndex(dlg._pick.findData("Fehler merken"))
    assert dlg._word == "Fehler merken"
    assert "fehler märchen" in dlg._known.text()
    assert "Fehler merken" in dlg._intro.text()
    dlg.deleteLater()

"""Tests for the glossary / error-memory pop-out editor (examples/dictation)."""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import settings_dialogs as sd  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_add_and_remove_rows(app):
    data = ["Apfel"]
    dlg = sd.ListEditorDialog(
        title="Test",
        rows_provider=lambda: [(w, "", w) for w in data],
        on_remove=lambda k: data.remove(k),
        on_add=lambda t: data.append(t),
        empty_text="leer")
    assert dlg._list.count() == 1
    dlg._input.setText("Birne")
    dlg._add()
    assert data == ["Apfel", "Birne"]
    assert dlg._list.count() == 2
    dlg._remove("Apfel")
    assert data == ["Birne"]
    assert dlg._list.count() == 1


def test_edit_row_value(app):
    # a "misheard -> correct" style store, edit the correct value
    store = {"kaser": "Cursor"}
    dlg = sd.ListEditorDialog(
        title="Test",
        rows_provider=lambda: [(k, f"{k}  ->", v) for k, v in store.items()],
        on_remove=lambda k: store.pop(k, None),
        on_edit=lambda k, v: store.__setitem__(k, v),
        empty_text="leer")
    dlg._edit("kaser", "Cursor vor")
    assert store == {"kaser": "Cursor vor"}


def test_search_filters_rows(app):
    data = {"kaser": "Cursor", "haus": "Maus", "tastatuhr": "Tastatur"}
    dlg = sd.ListEditorDialog(
        title="Test",
        rows_provider=lambda: [(k, k, v) for k, v in data.items()],
        on_remove=lambda k: data.pop(k, None),
        on_edit=lambda k, v: data.__setitem__(k, v),
        empty_text="leer")
    assert dlg._list.count() == 3
    dlg._search.setText("haus")            # matches the key
    assert dlg._list.count() == 1
    dlg._search.setText("Cursor")          # matches the value
    assert dlg._list.count() == 1
    dlg._search.setText("zzz")             # no match
    assert dlg._list.item(0).text() == "Keine Treffer."
    dlg._search.setText("")                # cleared → all back
    assert dlg._list.count() == 3


def test_empty_text_shown_when_no_rows(app):
    dlg = sd.ListEditorDialog(
        title="Test",
        rows_provider=lambda: [],
        on_remove=lambda _k: None,
        empty_text="Noch nichts da")
    assert dlg._list.count() == 1
    assert dlg._list.item(0).text() == "Noch nichts da"


def test_clear_all(app):
    data = ["a", "b", "c"]
    dlg = sd.ListEditorDialog(
        title="Test",
        rows_provider=lambda: [(w, "", w) for w in data],
        on_remove=lambda k: data.remove(k),
        on_clear=data.clear,
        empty_text="leer")
    assert dlg._list.count() == 3
    dlg._clear_all()
    assert data == []
    assert dlg._list.item(0).text() == "leer"


def test_enrollment_back_and_replace(app):
    discarded = []
    counter = [0]

    def on_stop(_prompt):
        counter[0] += 1
        return f"stamp{counter[0]}"

    dlg = sd.EnrollmentDialog(
        ["Satz A", "Satz B", "Satz C"],
        on_start=lambda: True, on_stop=on_stop,
        on_discard=discarded.append)
    dlg._toggle()                       # start recording sentence 0
    assert dlg._recording
    dlg._toggle()                       # stop → stamp1, advance to 1
    assert dlg._saved == {0: "stamp1"} and dlg._index == 1
    dlg._back()                         # misspoke → go back to sentence 0
    assert dlg._index == 0
    dlg._toggle()
    dlg._toggle()                       # re-record → replaces stamp1
    assert discarded == ["stamp1"]
    assert dlg._saved[0] == "stamp2"


def test_no_add_field_when_on_add_missing(app):
    dlg = sd.ListEditorDialog(
        title="Test",
        rows_provider=lambda: [("x", "x  ->", "y")],
        on_remove=lambda _k: None)
    assert dlg._input is None

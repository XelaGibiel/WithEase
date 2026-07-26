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
        rows_provider=lambda: [(w, w) for w in data],
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
        rows_provider=lambda: [(w, w) for w in data],
        on_remove=lambda k: data.remove(k),
        on_clear=data.clear,
        empty_text="leer")
    assert dlg._list.count() == 3
    dlg._clear_all()
    assert data == []
    assert dlg._list.item(0).text() == "leer"


def test_no_add_field_when_on_add_missing(app):
    dlg = sd.ListEditorDialog(
        title="Test",
        rows_provider=lambda: [("x → y", "x")],
        on_remove=lambda _k: None)
    assert dlg._input is None

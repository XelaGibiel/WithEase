"""Tests for the unified custom dictionary (examples/dictation module)."""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

import module  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make(app, settings=None):
    m = module.DictationModule()
    m._settings = settings if settings is not None else {}
    return m


def rows(m, cat="all"):
    # dictionary_rows yields (kind, key, trigger, result, src); tests care about
    # (result, trigger, src) i.e. (written/correct, spoken/misheard, origin).
    return [(result, trigger, src)
            for _kind, _key, trigger, result, src in m.dictionary_rows(cat)]


def test_migrates_legacy_glossary_and_spoken_forms(app):
    m = make(app, {"glossary": "Leibig, WithEase",
                   "spoken_forms": [["with ease", "WithEase"],
                                    ["kju bert", "QBert"]]})
    got = rows(m)
    # WithEase existed in both lists → kept once, with its spoken form
    assert ("WithEase", "with ease", "ich") in got
    assert ("QBert", "kju bert", "ich") in got
    assert ("Leibig", "", "gelernt") in got
    assert len(got) == 3
    # legacy keys are replaced by the unified list
    assert "dictionary" in m._settings


def test_category_filters(app):
    m = make(app, {"dictionary": [
        {"w": "WithEase", "s": "with ease", "src": "user"},
        {"w": "Leibig", "s": "", "src": "learned"},
        {"w": "Foo", "s": "", "src": "import"}]})
    assert [w for w, _s, _src in rows(m, "user")] == ["WithEase"]
    assert [w for w, _s, _src in rows(m, "learned")] == ["Leibig"]
    assert [w for w, _s, _src in rows(m, "import")] == ["Foo"]
    assert [w for w, _s, _src in rows(m, "spoken")] == ["WithEase"]


def test_add_edit_remove_and_derived_views(app):
    m = make(app)
    m.add_dictionary_entry("WithEase", "with ease", "user")
    m.add_dictionary_entry("Leibig", "", "learned")
    assert m.spoken_forms() == [("with ease", "WithEase")]
    assert set(m.glossary_words()) == {"WithEase", "Leibig"}
    # editing only the spoken form keeps the written key
    m.edit_dictionary_entry("WithEase", "WithEase", "with ease bitte")
    assert m.spoken_forms() == [("with ease bitte", "WithEase")]
    m.remove_dictionary_entry("Leibig")
    assert [w for w, _s, _src in rows(m)] == ["WithEase"]


def test_export_import_roundtrip(app, tmp_path):
    m = make(app)
    m.add_dictionary_entry("WithEase", "with ease")
    m.add_dictionary_entry("Leibig", "")          # bare term, no spoken form
    path = str(tmp_path / "dict.txt")
    assert m.export_dictionary(path) == 2
    # a hand-added spoken entry and a bare term
    with open(path, "a", encoding="utf-8") as f:
        f.write("foo = Foo\nnur ein begriff\n")
    m2 = make(app)
    assert m2.import_dictionary(path) == 4
    got = rows(m2)
    assert ("WithEase", "with ease", "Import") in got
    assert ("Foo", "foo", "Import") in got
    assert ("nur ein begriff", "", "Import") in got


def test_corrections_show_as_category_but_stay_in_memory(app):
    m = make(app)
    m._learn_correction("Kaser", "Cursor")        # smart engine learns it
    # appears in the unified list as a "mem" row under category „corrected"
    corr = m.dictionary_rows("corrected")
    assert any(kind == "mem" and result == "Cursor"
               for kind, _k, _t, result, _s in corr)
    assert any(kind == "mem" for kind, *_ in m.dictionary_rows("all"))
    # the plain dictionary categories never include corrections
    assert all(kind == "dict" for kind, *_ in m.dictionary_rows("user"))
    # …and the correction still lives in the error-memory engine
    assert "cursor" in {v.casefold()
                        for v in m._memory().substitutions().values()}


def test_unified_edit_remove_routes_to_the_right_store(app):
    m = make(app)
    m.add_dictionary_entry("WithEase", "with ease", "user")
    m._learn_correction("Kaser", "Cursor")
    key = m.dictionary_rows("corrected")[0][1]    # folded misheard key
    # editing a "mem" row changes the correction target (via the engine)
    m.dictionary_edit("mem", key, key, "Zeiger")
    assert m._memory().substitutions()[key] == "Zeiger"
    # editing a "dict" row updates the written form, keeps the spoken form
    m.dictionary_edit("dict", "WithEase", "with ease", "WithEase!")
    assert ("with ease", "WithEase!") in m.spoken_forms()
    # removing routes correctly
    m.dictionary_remove("mem", key)
    assert key not in m._memory().substitutions()
    m.dictionary_remove("dict", "WithEase!")
    assert m.spoken_forms() == []


def test_clear_category_bulk_removes(app):
    m = make(app, {"dictionary": [
        {"w": "Leibig", "s": "", "src": "user"},
        {"w": "Juli", "s": "", "src": "learned"},
        {"w": "Damen", "s": "", "src": "learned"},
        {"w": "WithEase", "s": "with ease", "src": "user"}]})
    assert m.clear_dictionary_category("learned") == 2
    assert {w for _kd, _k, _t, w, _s in m.dictionary_rows("all")} == {
        "Leibig", "WithEase"}
    # corrected clears the error memory
    m._learn_correction("Kaser", "Cursor")
    assert m.clear_dictionary_category("corrected") == 1
    assert m._memory().substitutions() == {}

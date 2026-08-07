"""P3 part C: exporting macros to a file and importing them back."""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from withease.modules.macros import MacrosModule  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _widget(app, macros):
    from withease.gui.settings.macros_settings import MacrosSettingsWidget
    mod = MacrosModule()
    mod.load_settings({"macros": macros})
    return MacrosSettingsWidget(mod), mod


def _silence(monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)


def test_export_then_import_roundtrip(app, tmp_path, monkeypatch):
    _silence(monkeypatch)
    out = tmp_path / "macros.json"

    src = [
        {"id": "a", "label": "Gruß", "trigger_key": "'g'", "type": "text",
         "payload": {"text": "Hallo"}, "category": "E-Mail", "uses": 5},
        {"id": "b", "label": "Word", "trigger_key": "'w'", "type": "app",
         "payload": {"path": "notepad.exe"}, "category": "Büro"},
    ]
    w, _ = _widget(app, src)
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(out), ""))
    w._on_export()

    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["withease_macros"] == 1
    assert [m["label"] for m in saved["macros"]] == ["Gruß", "Word"]
    # the personal usage counter is not shared
    assert all("uses" not in m for m in saved["macros"])
    assert saved["macros"][0]["category"] == "E-Mail"

    # import into a fresh, empty module
    w2, mod2 = _widget(app, [])
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(out), ""))
    w2._on_import()

    labels = [m.label for m in mod2._macros]
    assert labels == ["Gruß", "Word"]
    assert mod2._macros[0].category == "E-Mail"
    assert all(m.uses == 0 for m in mod2._macros)     # reset on import
    # fresh ids so an import never clashes with existing macros
    assert {m.id for m in mod2._macros} != {"a", "b"}


def test_import_invalid_file_is_safe(app, tmp_path, monkeypatch):
    _silence(monkeypatch)
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")
    w, mod = _widget(app, [])
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(bad), ""))
    w._on_import()                 # must not raise
    assert mod._macros == []

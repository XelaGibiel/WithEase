"""P3: the macro editor dialog carries category through and never drops the
usage counter when a macro is edited."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from withease.modules.macros import Macro  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_edit_preserves_category_and_uses(app):
    from withease.gui.settings.macros_settings import _MacroDialog
    macro = Macro(id="m1", label="Gruß", trigger_key="'g'", type="text",
                  payload={"text": "Hallo"}, category="E-Mail", uses=9)
    dlg = _MacroDialog(macro=macro, categories=["E-Mail", "Word"])
    data = dlg.result_data()
    assert data["id"] == "m1"
    assert data["category"] == "E-Mail"
    assert data["uses"] == 9            # counter must survive an edit


def test_new_macro_defaults(app):
    from withease.gui.settings.macros_settings import _MacroDialog
    dlg = _MacroDialog(categories=["E-Mail"])
    data = dlg.result_data()
    assert data["category"] == ""
    assert data["uses"] == 0


def test_move_macro_reorders_the_list(app):
    from withease.modules.macros import MacrosModule
    from withease.gui.settings.macros_settings import MacrosSettingsWidget
    mod = MacrosModule()
    mod.load_settings({"macros": [
        {"id": "a", "label": "A", "trigger_key": "'a'", "type": "text"},
        {"id": "b", "label": "B", "trigger_key": "'b'", "type": "text"},
        {"id": "c", "label": "C", "trigger_key": "'c'", "type": "text"},
    ]})
    w = MacrosSettingsWidget(mod)
    w._table.selectRow(2)
    w._move_macro(-1)
    assert [m.label for m in mod._macros] == ["A", "C", "B"]
    w._table.selectRow(0)
    w._move_macro(-1)                       # already at top → no-op
    assert [m.label for m in mod._macros] == ["A", "C", "B"]

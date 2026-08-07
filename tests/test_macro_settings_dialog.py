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

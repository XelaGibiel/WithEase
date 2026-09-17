"""A module switches on from anywhere on its row.

Reported from real use: turning a module on in the settings meant hitting the
checkbox itself.  The switch is a title across the full width of the page, but
Qt only reacts to a click on the small box and its caption text - most of the
visible row did nothing.  For someone with a tremor that is five tries instead
of one.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "dictation"))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QSizePolicy  # noqa: E402

from withease.gui.ui_utils import whole_row_toggle  # noqa: E402

LEFT = Qt.MouseButton.LeftButton
NONE = Qt.KeyboardModifier.NoModifier
FAR_RIGHT = QPoint(560, 22)          # well beside the caption


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def row(app, helper=True):
    box = QCheckBox("Maus aktivieren")
    if helper:
        whole_row_toggle(box)
    box.resize(600, 44)
    box.show()
    return box


def test_the_premise_a_plain_checkbox_ignores_that_click(app):
    """Without this the tests below would prove nothing."""
    box = row(app, helper=False)
    QTest.mouseClick(box, LEFT, NONE, FAR_RIGHT)
    assert not box.isChecked()


def test_a_click_anywhere_on_the_row_toggles(app):
    box = row(app)
    QTest.mouseClick(box, LEFT, NONE, FAR_RIGHT)
    assert box.isChecked()
    QTest.mouseClick(box, LEFT, NONE, FAR_RIGHT)
    assert not box.isChecked()


def test_the_toggled_signal_fires_exactly_once(app):
    box = row(app)
    seen = []
    box.toggled.connect(seen.append)
    QTest.mouseClick(box, LEFT, NONE, FAR_RIGHT)
    assert seen == [True]


def test_a_click_on_the_box_itself_still_works(app):
    box = row(app)
    QTest.mouseClick(box, LEFT, NONE, QPoint(10, 22))
    assert box.isChecked()


def test_pressing_and_sliding_off_the_row_does_nothing(app):
    """Like a real button: a press that ends somewhere else is a change of
    mind, not a click."""
    box = row(app)
    QTest.mousePress(box, LEFT, NONE, FAR_RIGHT)
    QTest.mouseRelease(box, LEFT, NONE, QPoint(560, 300))
    assert not box.isChecked()


def test_a_disabled_row_stays_as_it_is(app):
    box = row(app)
    box.setEnabled(False)
    QTest.mouseClick(box, LEFT, NONE, FAR_RIGHT)
    assert not box.isChecked()


def test_the_keyboard_still_toggles(app):
    box = row(app)
    QTest.keyClick(box, Qt.Key.Key_Space)
    assert box.isChecked()


def test_the_row_says_it_is_clickable(app):
    box = row(app)
    assert box.cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert box.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding


# -- every module page uses it -----------------------------------------------

def _enable_boxes():
    from withease.gui.settings.keyboard_settings import KeyboardSettingsWidget
    from withease.gui.settings.macros_settings import MacrosSettingsWidget
    from withease.gui.settings.mouse_settings import MouseSettingsWidget
    from withease.modules.keyboard import KeyboardModule
    from withease.modules.macros import MacrosModule
    from withease.modules.mouse import MouseModule
    import module as dictation

    pages = {
        "Tastatur": KeyboardSettingsWidget(KeyboardModule()),
        "Maus": MouseSettingsWidget(MouseModule()),
        "Makros": MacrosSettingsWidget(MacrosModule()),
        "Diktieren": dictation.DictationModule().get_settings_widget(),
    }
    return {name: page._enabled_cb for name, page in pages.items()}


def test_every_module_switch_toggles_from_the_whole_row(app):
    # Checked by what the helper leaves behind rather than by clicking:
    # a click would really start the module, and with it global key hooks.
    for name, box in _enable_boxes().items():
        assert box.cursor().shape() == Qt.CursorShape.PointingHandCursor, name
        assert (box.sizePolicy().horizontalPolicy()
                == QSizePolicy.Policy.Expanding), name

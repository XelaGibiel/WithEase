"""Hint icons: hover shows the tip, a click pins it until clicked again -
for anyone whose hand does not stay still on a tiny icon."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    from withease.gui import theme
    qt = QApplication.instance() or QApplication([])
    theme.apply_theme(qt, "light")
    return qt


@pytest.fixture
def page(app):
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    from withease.gui.widgets.hint_icon import HintIcon
    w = QWidget()
    lay = QVBoxLayout(w)
    icons = [HintIcon(f"Erklärung {i}: ein ganzer Satz zum Lesen.")
             for i in range(2)]
    for icon in icons:
        lay.addWidget(icon)
    w.resize(300, 200)
    w.show()
    app.processEvents()
    w.icons = icons
    yield w
    for icon in icons:
        icon.unpin()
    w.close()


def _click(widget):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)


def test_a_click_pins_and_a_second_click_closes(app, page):
    icon = page.icons[0]
    _click(icon)
    assert icon.is_pinned() and icon._pinned.isVisible()
    assert icon.property("pinned") is True
    assert icon.toolTip() == ""             # no hover tip on top of it
    from PySide6.QtWidgets import QLabel
    text = icon._pinned.findChild(QLabel, "pinnedTipText")
    assert "Erklärung 0" in text.text()
    app.processEvents()
    assert icon.is_pinned()                 # moving away does not close it
    _click(icon)
    assert not icon.is_pinned()
    assert "Erklärung 0" in icon.toolTip()  # hover works again


def test_clicking_the_pinned_tip_closes_it(app, page):
    icon = page.icons[0]
    icon.pin()
    _click(icon._pinned)
    assert not icon.is_pinned()


def test_only_one_tip_is_pinned_at_a_time(app, page):
    first, second = page.icons
    first.pin()
    second.pin()
    assert second.is_pinned() and not first.is_pinned()


def test_the_keyboard_pins_too(app, page):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    icon = page.icons[1]
    icon.setFocus()
    QTest.keyClick(icon, Qt.Key.Key_Space)
    assert icon.is_pinned()
    QTest.keyClick(icon, Qt.Key.Key_Escape)
    assert not icon.is_pinned()


def test_a_hidden_icon_takes_its_tip_along(app, page):
    icon = page.icons[0]
    icon.pin()
    page.hide()
    app.processEvents()
    assert not icon.is_pinned()


def test_hiding_all_hints_unpins(app, page):
    from withease.gui.widgets.hint_icon import set_hints_visible
    icon = page.icons[0]
    icon.pin()
    set_hints_visible(False)
    try:
        assert not icon.is_pinned()
    finally:
        set_hints_visible(True)

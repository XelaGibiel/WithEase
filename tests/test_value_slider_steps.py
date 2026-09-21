"""Sliders with a handful of clear positions, and no accidental changes
from the mouse wheel or Page Up / Page Down."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    from withease.gui import theme
    qt = QApplication.instance() or QApplication([])
    theme.apply_theme(qt, "light")              # installs the wheel guard
    return qt


def test_a_stepped_slider_stops_only_at_its_steps(app):
    from withease.gui.widgets.value_slider import ValueSlider
    s = ValueSlider(30, 210, suffix=" px", step=20)
    got = []
    s.valueChanged.connect(got.append)
    s.setValue(90)
    assert s.value() == 90
    s.setValue(97)                               # between two steps
    assert s.value() == 90
    s.setValue(101)
    assert s.value() == 110 and got[-1] == 110
    assert (s.minimum(), s.maximum()) == (30, 210)
    assert s.slider.maximum() == 9               # ten positions
    s.setValue(999)
    assert s.value() == 210


def test_without_a_step_nothing_changes(app):
    from withease.gui.widgets.value_slider import ValueSlider
    s = ValueSlider(1, 10)
    s.setValue(7)
    assert s.value() == 7 and s.slider.maximum() == 9


def _page_with_slider(app):
    from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget
    from withease.gui.widgets.value_slider import ValueSlider
    area = QScrollArea()
    inner = QWidget()
    lay = QVBoxLayout(inner)
    slider = ValueSlider(0, 50, suffix=" %", step=5)
    slider.setValue(25)
    lay.addWidget(slider)
    lay.addSpacing(3000)
    area.setWidget(inner)
    area.resize(300, 200)
    area.show()
    app.processEvents()
    return area, slider


def test_the_wheel_scrolls_the_page_not_the_slider(app):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    area, slider = _page_with_slider(app)
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0),
                        QPoint(0, -120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(slider.slider, event)
    assert slider.value() == 25
    assert area.verticalScrollBar().value() > 0
    area.close()


def test_page_down_on_a_slider_moves_the_page(app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    area, slider = _page_with_slider(app)
    slider.slider.setFocus()
    QTest.keyClick(slider.slider, Qt.Key.Key_PageDown)
    assert slider.value() == 25
    assert area.verticalScrollBar().value() > 0
    QTest.keyClick(slider.slider, Qt.Key.Key_Right)      # arrows still work
    assert slider.value() == 30
    area.close()


def test_the_scroll_bar_itself_still_scrolls(app):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    area, slider = _page_with_slider(app)
    bar = area.verticalScrollBar()
    event = QWheelEvent(QPointF(2, 2), QPointF(2, 2), QPoint(0, 0),
                        QPoint(0, -120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(bar, event)
    assert bar.value() > 0
    area.close()


def test_the_steps_are_written_under_a_stepped_slider(app):
    from withease.gui.widgets.value_slider import ValueSlider
    stepped = ValueSlider(30, 210, suffix=" px", step=20)
    plain = ValueSlider(1, 10)
    assert stepped._steps is not None and plain._steps is None
    assert stepped.step_values() == [30, 50, 70, 90, 110, 130, 150, 170,
                                     190, 210]
    stepped.resize(400, 80)
    centres = stepped._handle_centres()
    assert len(centres) == 10 and centres == sorted(centres)

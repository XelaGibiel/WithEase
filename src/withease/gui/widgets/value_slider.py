"""ValueSlider – a slider that always shows its current value beside it.

A bare slider only tells you *roughly* where a setting sits: you can see the
handle is somewhere past the middle, but not that "past the middle" means 6
or 7.  For settings that are later described in words ("Pfeilstärke 6 px") the
number is the part that actually matters, so it is shown permanently rather
than only while dragging.

Drop-in for ``QSlider`` at the call sites in this app: it forwards the slider
API those use (``setRange``/``setValue``/``value``/``setTickPosition``/
``setTickInterval``) and re-emits ``valueChanged``, so a form row keeps working
unchanged – including ``QFormLayout.setRowVisible(slider, …)``, which looks the
row up by its field widget.

    s = ValueSlider(1, 10)
    s = ValueSlider(40, 200, suffix=" px")
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget

from withease.gui import theme


class ValueSlider(QWidget):
    """Horizontal slider + a read-out of its current value."""

    valueChanged = Signal(int)  # noqa: N815 – mirrors QSlider's signal name

    def __init__(self, minimum: int = 0, maximum: int = 100,
                 suffix: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._suffix = suffix

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(minimum, maximum)
        # The click target rule that compact_fields() applies to a bare slider
        # has to be set here instead: from the form's point of view the field
        # is now this container, not the slider inside it.
        self._slider.setMinimumHeight(theme.target_px())
        self._slider.valueChanged.connect(self._on_value)
        row.addWidget(self._slider, 1)

        self._value_lbl = QLabel()
        self._value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight
                                     | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._value_lbl)

        self._refresh_label()

    # -- QSlider surface used by the settings pages ------------------------

    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802
        self._slider.setRange(minimum, maximum)
        self._refresh_label()

    def setValue(self, value: int) -> None:  # noqa: N802
        self._slider.setValue(value)

    def value(self) -> int:
        return self._slider.value()

    def setTickPosition(self, position) -> None:  # noqa: N802
        self._slider.setTickPosition(position)

    def setTickInterval(self, interval: int) -> None:  # noqa: N802
        self._slider.setTickInterval(interval)

    @property
    def slider(self) -> QSlider:
        """The wrapped QSlider, for anything the forwarding above misses."""
        return self._slider

    # -- internals ---------------------------------------------------------

    def _on_value(self, value: int) -> None:
        self._value_lbl.setText(self._format(value))
        self.valueChanged.emit(value)

    def _format(self, value: int) -> str:
        return f"{value}{self._suffix}"

    def _refresh_label(self) -> None:
        # A fixed width sized for the WIDEST possible reading: otherwise the
        # slider gets shorter as the number grows ("9 px" → "120 px") and the
        # handle drifts sideways while the value stays the same.
        fm = QFontMetrics(self._value_lbl.font())
        widest = max(fm.horizontalAdvance(self._format(v))
                     for v in (self._slider.minimum(), self._slider.maximum()))
        self._value_lbl.setFixedWidth(widest + 4)
        self._value_lbl.setText(self._format(self._slider.value()))

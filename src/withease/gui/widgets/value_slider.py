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
    s = ValueSlider(30, 210, suffix=" px", step=20)   # 10 positions

``step``: the slider only stops at ``minimum + n * step``.  A handful of
clearly different positions are easier to hit than 170 barely different
ones - and the value shown is always one of them.
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
                 suffix: str = "", parent: QWidget | None = None,
                 step: int = 1) -> None:
        super().__init__(parent)
        self._suffix = suffix
        self._min = minimum
        self._step = max(1, int(step))

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        # The QSlider itself counts positions 0..n; the value is
        # minimum + position * step.
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, self._positions(minimum, maximum))
        self._slider.setSingleStep(1)
        self._slider.setPageStep(1)
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
        self._min = minimum
        self._slider.setRange(0, self._positions(minimum, maximum))
        self._refresh_label()

    def setValue(self, value: int) -> None:  # noqa: N802
        """A value between the steps goes to the nearest one."""
        self._slider.setValue(round((int(value) - self._min) / self._step))

    def value(self) -> int:
        return self._to_value(self._slider.value())

    def minimum(self) -> int:
        return self._min

    def maximum(self) -> int:
        return self._to_value(self._slider.maximum())

    def setTickPosition(self, position) -> None:  # noqa: N802
        self._slider.setTickPosition(position)

    def setTickInterval(self, interval: int) -> None:  # noqa: N802
        """In the slider's own units (px, %), like QSlider."""
        self._slider.setTickInterval(max(1, round(interval / self._step)))

    @property
    def slider(self) -> QSlider:
        """The wrapped QSlider, for anything the forwarding above misses."""
        return self._slider

    # -- internals ---------------------------------------------------------

    def _positions(self, minimum: int, maximum: int) -> int:
        return max(0, (int(maximum) - int(minimum)) // self._step)

    def _to_value(self, position: int) -> int:
        return self._min + position * self._step

    def _on_value(self, position: int) -> None:
        value = self._to_value(position)
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
                     for v in (self.minimum(), self.maximum()))
        self._value_lbl.setFixedWidth(widest + 4)
        self._value_lbl.setText(self._format(self.value()))

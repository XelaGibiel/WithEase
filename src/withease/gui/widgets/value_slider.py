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
ones - and the value shown is always one of them.  A stepped slider writes
every step under itself, so you can see where each position is before
moving there (``show_steps`` switches that on or off explicitly).
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

from withease.gui import theme


class ValueSlider(QWidget):
    """Horizontal slider + a read-out of its current value."""

    valueChanged = Signal(int)  # noqa: N815 – mirrors QSlider's signal name

    def __init__(self, minimum: int = 0, maximum: int = 100,
                 suffix: str = "", parent: QWidget | None = None,
                 step: int = 1, show_steps: bool | None = None) -> None:
        super().__init__(parent)
        self._suffix = suffix
        self._min = minimum
        self._step = max(1, int(step))
        # a slider takes the width it gets - also inside a wrapper row
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)

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
        if show_steps if show_steps is not None else self._step > 1:
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(0)
            column.addWidget(self._slider)
            self._steps = _StepLabels(self)
            column.addWidget(self._steps)
            row.addLayout(column, 1)
        else:
            self._steps = None
            row.addWidget(self._slider, 1)

        self._value_lbl = QLabel()
        self._value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight
                                     | Qt.AlignmentFlag.AlignVCenter)
        # level with the slider line, not with the step labels under it
        self._value_lbl.setFixedHeight(theme.target_px())
        row.addWidget(self._value_lbl, 0, Qt.AlignmentFlag.AlignTop)

        self._refresh_label()

    # -- QSlider surface used by the settings pages ------------------------

    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802
        self._min = minimum
        self._slider.setRange(0, self._positions(minimum, maximum))
        self._refresh_label()
        if self._steps is not None:
            self._steps.update()

    def step_values(self) -> list[int]:
        """Every value the slider can stop at."""
        return [self._to_value(i) for i in range(self._slider.maximum() + 1)]

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

    def _handle_centres(self) -> list[float]:
        """Where the handle's centre sits for each position, in this
        widget's slider coordinates - the labels go right under it."""
        opt = QStyleOptionSlider()
        self._slider.initStyleOption(opt)
        handle = self._slider.style().pixelMetric(
            QStyle.PixelMetric.PM_SliderLength, opt, self._slider)
        span = max(1, self._slider.width() - handle)
        n = max(1, self._slider.maximum())
        return [handle / 2 + span * i / n
                for i in range(self._slider.maximum() + 1)]

    def _positions(self, minimum: int, maximum: int) -> int:
        return max(0, (int(maximum) - int(minimum)) // self._step)

    def _to_value(self, position: int) -> int:
        return self._min + position * self._step

    def _on_value(self, position: int) -> None:
        value = self._to_value(position)
        self._value_lbl.setText(self._format(value))
        if self._steps is not None:
            self._steps.update()
        self.valueChanged.emit(value)

    def _format(self, value: int) -> str:
        return f"{value}{self._suffix}"

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._steps is not None:
            self._steps.update()

    def _refresh_label(self) -> None:
        # A fixed width sized for the WIDEST possible reading: otherwise the
        # slider gets shorter as the number grows ("9 px" → "120 px") and the
        # handle drifts sideways while the value stays the same.
        fm = QFontMetrics(self._value_lbl.font())
        widest = max(fm.horizontalAdvance(self._format(v))
                     for v in (self.minimum(), self.maximum()))
        self._value_lbl.setFixedWidth(widest + 4)
        self._value_lbl.setText(self._format(self.value()))


class _StepLabels(QWidget):
    """Every step of a ValueSlider, written under the position it belongs
    to, with a short tick.  The current one is in the accent colour."""

    def __init__(self, owner: ValueSlider) -> None:
        super().__init__(owner)
        self._owner = owner
        fm = QFontMetrics(self._font())
        self.setFixedHeight(fm.height() + 6)

    def _font(self):
        font = self.font()
        size = font.pointSizeF()
        if size > 0:
            font.setPointSizeF(max(7.0, size * 0.85))
        return font

    def paintEvent(self, _event) -> None:  # noqa: N802
        owner = self._owner
        values = owner.step_values()
        if not values:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = self._font()
        p.setFont(font)
        fm = QFontMetrics(font)
        hint = QColor(theme.hint_color())
        accent = QColor(theme.accent())
        current = owner.value()
        x0 = owner._slider.x() - self.x()
        centres = owner._handle_centres()
        # thin the labels out when they would touch (narrow window)
        widest = max(fm.horizontalAdvance(str(v)) for v in values) + 6
        room = (centres[-1] - centres[0]) / max(1, len(values) - 1)
        every = max(1, int(-(-widest // max(1.0, room))))
        last = len(values) - 1
        labelled = set(range(0, last + 1, every)) | {last}
        before = max((i for i in labelled if i < last), default=None)
        if before is not None and last - before < every:
            labelled.discard(before)
        for i, (value, centre) in enumerate(zip(values, centres)):
            x = x0 + centre
            on = value == current
            p.setPen(accent if on else hint)
            p.drawLine(int(x), 0, int(x), 3)
            if i not in labelled:
                continue
            text = str(value)
            w = fm.horizontalAdvance(text)
            left = min(max(0.0, x - w / 2), self.width() - w)
            font.setBold(on)
            p.setFont(font)
            p.drawText(QRectF(left, 4, w + 2, fm.height()),
                       Qt.AlignmentFlag.AlignLeft, text)
        p.end()

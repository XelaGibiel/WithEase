"""A small "back to the default" button behind a single setting.

Resetting a whole section at once throws away the settings you were happy
with together with the one you wanted back.  ``ResetField`` wraps one
control and puts a ↺ button behind it that returns just that setting to its
default.  The button only shows while the value differs from the default -
its space is kept, so nothing shifts when it appears - and its tool-tip
says what the default is.
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolButton, QWidget

from withease.core.i18n import tr
from withease.gui import theme


class ResetField(QWidget):
    """``field`` with a reset button behind it.

    ``get_value()`` reads the current value, ``set_value(default)`` puts the
    default back (through the control, so it is saved the normal way).
    ``changed`` is the control's change signal; without one, call
    ``refresh()`` after changing the value by hand.  ``describe`` turns the
    default into words for the tool-tip ("90 px")."""

    def __init__(self, field: QWidget, default: Any,
                 get_value: Callable[[], Any],
                 set_value: Callable[[Any], None],
                 changed: Any = None,
                 describe: Callable[[Any], str] | None = None) -> None:
        super().__init__()
        self.field = field
        self._default = default
        self._get = get_value
        self._set = set_value
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        grows = (field.sizePolicy().horizontalPolicy()
                 in (QSizePolicy.Policy.Expanding,
                     QSizePolicy.Policy.MinimumExpanding))
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)
        row.addWidget(field, 1 if grows else 0)
        if not grows:
            row.addStretch(1)       # the buttons line up at the right edge
        self.button = QToolButton()
        self.button.setObjectName("resetButton")
        self.button.setText("↺")
        side = theme.target_px()
        self.button.setFixedSize(side, side)
        font = self.button.font()
        if font.pointSizeF() > 0:           # big enough to see and to hit
            font.setPointSizeF(font.pointSizeF() * 1.5)
        font.setBold(True)
        self.button.setFont(font)
        text = describe(default) if describe else str(default)
        tip = tr("settings.reset_one", value=text)
        self.button.setToolTip(tip)
        self.button.setAccessibleName(tip)
        policy = self.button.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.button.setSizePolicy(policy)
        self.button.clicked.connect(self._reset)
        row.addWidget(self.button)
        if changed is not None:
            changed.connect(lambda *_: self.refresh())
        self.refresh()

    def is_default(self) -> bool:
        return self._get() == self._default

    def refresh(self) -> None:
        self.button.setVisible(not self.is_default())

    def _reset(self) -> None:
        self._set(self._default)
        self.refresh()

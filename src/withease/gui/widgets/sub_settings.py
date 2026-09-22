"""Making it visible which settings belong together.

Two small building blocks for settings pages:

* ``group_heading`` - a quiet heading that splits a long section into the
  questions people actually ask ("Wann erscheint es?", "Wie sieht es aus?").
* ``SubSettings`` - the options that only exist because of the switch right
  above them.  They sit indented behind a line in the accent colour, so it
  is obvious at a glance which switch they belong to; hiding the whole box
  hides them together.

The look comes from the theme (objectNames ``settingsGroup`` and
``subSettings``), so it follows light, dark and high-contrast mode.
"""
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QFormLayout, QFrame, QLabel


def group_heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("settingsGroup")
    return label


class SubSettings(QFrame):
    """A box for dependent settings; fill ``self.form`` like any form."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("subSettings")
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 2, 0, 2)
        self.form.setSpacing(8)
        self.form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        """As tall as the rows need at the width the box REALLY has.

        The form works its hint out for a width of its own choosing - far
        narrower than the page - so every wrapped note inside counted a line
        or two too many, and the box ended in an empty stretch of accent
        line under its last setting."""
        hint = super().sizeHint()
        if self.width() > 0 and self.form.hasHeightForWidth():
            return QSize(hint.width(),
                         self.form.totalHeightForWidth(self.width()))
        return hint

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self.updateGeometry()       # the hint depends on the width

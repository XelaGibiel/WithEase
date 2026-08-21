"""A wrapping horizontal layout.

Buttons (or any widgets) flow left-to-right and wrap to the next line when the
container gets too narrow, instead of a QHBoxLayout squeezing every item – which
shrank the ▲▼ reorder buttons to a sliver and pushed card borders off-screen.
Adapted from the classic Qt FlowLayout example.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None,
                 margin: int = 0, h_spacing: int = 6, v_spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_space = h_spacing
        self._v_space = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    # -- QLayout plumbing ------------------------------------------------
    def addItem(self, item: QLayoutItem) -> None:      # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:   # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:   # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:   # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:               # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:       # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:        # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:                       # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:                    # noqa: N802
        # Min width = the widest single item, so the container can shrink down
        # to one column (everything else wraps) and never forces overflow.
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # -- layout core -----------------------------------------------------
    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = eff.x(), eff.y(), 0
        for item in self._items:
            # Respect the widget's own min/max (e.g. a setFixedWidth button),
            # otherwise a bare sizeHint would ignore it and paint a sliver.
            size = item.sizeHint().expandedTo(
                item.minimumSize()).boundedTo(item.maximumSize())
            next_x = x + size.width() + self._h_space
            if next_x - self._h_space > eff.right() and line_h > 0:
                x = eff.x()
                y = y + line_h + self._v_space
                next_x = x + size.width() + self._h_space
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), size))
            x = next_x
            line_h = max(line_h, size.height())
        return y + line_h - rect.y() + m.bottom()

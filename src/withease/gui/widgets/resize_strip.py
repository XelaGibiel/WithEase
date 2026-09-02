"""ResizeStrip – a drag handle that resizes the widget above it.

Used below the long tables (macros, actions) so the user can pull the list
taller when they have many entries, instead of scrolling inside a fixed-height
box.  Shared so every such table gets exactly the same grip and behaviour.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QWidget

from withease.core.i18n import tr


class ResizeStrip(QWidget):
    """Drag handle below a table.

    Shows a row of dots (⠿-style grip) so it is immediately obvious that the
    area can be resized.  Dragging it down/up sets the table's fixed height."""

    _FLOOR_H = 80   # fallback when the target has no fixed height yet
    _DOT_R = 2      # dot radius px
    _DOT_COLS = 6   # dots per row
    _DOT_ROWS = 2   # rows of dots
    _DOT_GAP = 5    # gap between dot centres

    def __init__(self, target: QWidget,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._target = target
        self._drag_start: QPoint | None = None
        self._start_h: int = 0
        # The height the table starts at is also its minimum: the handle is
        # there to make a long list BIGGER.  Shrinking below the default only
        # squeezed the surrounding card until its heading was pushed out of
        # place, and nobody wants a list smaller than the standard one.
        # setFixedHeight() (called by both call sites before constructing the
        # strip) sets minimumHeight, which is reliable before the first layout
        # pass – unlike height(), which is still 0 at this point.
        self._min_h = max(target.minimumHeight(), self._FLOOR_H)
        total_h = self._DOT_ROWS * self._DOT_R * 2 + (self._DOT_ROWS - 1) * self._DOT_GAP
        self.setFixedHeight(total_h + 10)   # 10 px vertical padding
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip(tr("ui.resize_strip.hint"))

    def paintEvent(self, event) -> None:  # noqa: N802
        from PySide6.QtGui import QPainter
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = self.palette().color(self.foregroundRole())
        col.setAlphaF(0.35)
        painter.setBrush(col)
        painter.setPen(Qt.PenStyle.NoPen)
        r = self._DOT_R
        cols, rows, gap = self._DOT_COLS, self._DOT_ROWS, self._DOT_GAP
        grid_w = cols * r * 2 + (cols - 1) * (gap - r * 2)
        grid_h = rows * r * 2 + (rows - 1) * (gap - r * 2)
        ox = (self.width() - grid_w) // 2
        oy = (self.height() - grid_h) // 2
        for row in range(rows):
            for col in range(cols):
                x = ox + col * gap
                y = oy + row * gap
                painter.drawEllipse(x - r, y - r, r * 2, r * 2)
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
            self._start_h = self._target.height()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_start is not None:
            delta = event.globalPosition().toPoint().y() - self._drag_start.y()
            new_h = max(self._min_h, self._start_h + delta)
            self._target.setFixedHeight(new_h)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_start = None
        super().mouseReleaseEvent(event)

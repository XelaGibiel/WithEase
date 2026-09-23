"""A row of jump targets above a long settings page.

A page of seven cards is three to four screens tall, and the only way to a
card at the bottom was to scroll past everything in between.  Tabs would
fix the scrolling but hide six of the seven headings, which is worse: what
is not on screen is not remembered as existing.

The jump bar keeps the page whole - one column, one scroll, searchable and
readable from top to bottom - and adds the one thing tabs are good at: one
click to a section.  It stays put above the scrolling area, so the way back
is always in the same place.  A section that is collapsed opens when it is
jumped to.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QPushButton, QScrollArea, QWidget

from withease.gui import theme
from withease.gui.widgets.flow_layout import FlowLayout


class JumpBar(QWidget):
    """``add(name, widget)`` per section, ``attach(scroll_area)`` once."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("jumpBar")
        self._row = FlowLayout(self, margin=0, h_spacing=6, v_spacing=6)
        self._scroll: QScrollArea | None = None
        self._targets: list[tuple[str, QWidget]] = []

    def attach(self, scroll: QScrollArea) -> None:
        self._scroll = scroll

    def add(self, name: str, target: QWidget) -> QPushButton:
        button = QPushButton(name)
        button.setObjectName("jumpButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFlat(True)
        button.setMinimumHeight(theme.target_px())
        button.clicked.connect(lambda: self.jump_to(target))
        self._row.addWidget(button)
        self._targets.append((name, target))
        return button

    def names(self) -> list[str]:
        return [name for name, _target in self._targets]

    def jump_to(self, target: QWidget) -> None:
        """Show that section: open it if it is collapsed, then scroll it to
        the top of the page (deferred, so an opened section has grown by
        the time we scroll)."""
        for opener in ("set_open", "set_checked"):
            setter = getattr(target, opener, None)
            if callable(setter):
                try:
                    setter(True)
                except TypeError:
                    pass
                break

        def do_scroll() -> None:
            import shiboken6
            scroll = self._scroll
            if scroll is None or not shiboken6.isValid(target):
                return
            bar = scroll.verticalScrollBar()
            top = target.mapTo(scroll.widget(), target.rect().topLeft()).y()
            bar.setValue(min(bar.maximum(), max(0, top - 12)))
        QTimer.singleShot(0, do_scroll)

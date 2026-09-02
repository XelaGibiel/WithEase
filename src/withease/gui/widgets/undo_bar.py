"""A short-lived "done – undo?" bar, shown after something was deleted.

Why this exists instead of a confirmation dialog: a dialog makes the user
perform a SECOND precise click, on a small button, right after the one that
already went wrong.  For someone with a tremor that is the exact moment the
wrong button gets hit – and the dialog then reads as confirmation of an
intention nobody had.

Doing the thing and offering to take it back inverts the cost.  Nothing has to
be aimed at to keep the data safe; the risky click is the one that is now
optional, and it stays available for a good while.

The bar floats over the bottom of its window rather than sitting in the
layout, so nothing below it jumps while it is up.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from withease.core.i18n import tr
from withease.gui import theme

# Long on purpose.  The point is not to be dismissed quickly but to still be
# there once someone has realised what happened, walked their eyes back to the
# screen and aimed at the button.
DEFAULT_SECONDS = 20


class UndoBar(QFrame):
    """Floating bar with a message and one "Rückgängig" button."""

    _current: dict[int, "UndoBar"] = {}

    def __init__(self, window: QWidget, text: str, on_undo,
                 seconds: int = DEFAULT_SECONDS) -> None:
        super().__init__(window)
        self._on_undo = on_undo
        self.setObjectName("undoBar")
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(16)
        self._label = QLabel(text)
        self._label.setWordWrap(True)
        layout.addWidget(self._label, 1)

        self._button = QPushButton(tr("undo.action"))
        self._button.setMinimumHeight(theme.target_px())
        self._button.clicked.connect(self._undo)
        layout.addWidget(self._button)

        close = QPushButton("✕")
        close.setToolTip(tr("undo.dismiss"))
        close.setFixedSize(theme.target_px(), theme.target_px())
        close.clicked.connect(self._finish)
        layout.addWidget(close)

        self.setStyleSheet(
            f"QFrame#undoBar {{ background: {theme.card_bg()};"
            f" border: 2px solid {theme.accent()}; border-radius: 10px; }}")

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(1, seconds) * 1000)
        self._timer.timeout.connect(self._finish)

        # While the pointer rests on the bar (or the keyboard focus is in it)
        # the user is clearly still deciding – do not pull it away mid-thought.
        self._holder = _HoldWhileBusy(self)
        self.installEventFilter(self._holder)
        for child in (self._label, self._button, close):
            child.installEventFilter(self._holder)

        window.installEventFilter(_Reposition.get())
        self._place()
        self.show()
        self.raise_()
        self._timer.start()

    # -- placement -------------------------------------------------------

    def _place(self) -> None:
        window = self.parentWidget()
        if window is None:
            return
        width = min(max(360, window.width() - 80), window.width() - 24)
        self.setFixedWidth(max(240, width))
        self.adjustSize()
        x = (window.width() - self.width()) // 2
        y = window.height() - self.height() - 24
        self.move(max(12, x), max(12, y))

    # -- lifetime --------------------------------------------------------

    def _undo(self) -> None:
        callback, self._on_undo = self._on_undo, None
        self._close()
        if callback is not None:
            callback()

    def _finish(self) -> None:
        """Time is up (or dismissed): the deletion becomes permanent."""
        self._on_undo = None
        self._close()

    def _close(self) -> None:
        self._timer.stop()
        UndoBar._current.pop(id(self.parentWidget()), None)
        self.hide()
        self.deleteLater()

    def hold(self) -> None:
        self._timer.stop()

    def resume(self) -> None:
        if self._on_undo is not None and not self._timer.isActive():
            self._timer.start()

    # -- entry point -----------------------------------------------------

    @classmethod
    def show_undo(cls, widget: QWidget, text: str, on_undo,
                  seconds: int = DEFAULT_SECONDS) -> "UndoBar | None":
        """Show the bar over ``widget``'s window.  Returns the bar, or None if
        there is no window to put it on (never let this break the caller: the
        deletion itself has already happened)."""
        try:
            window = widget.window() if widget is not None else None
            if window is None:
                return None
            previous = cls._current.get(id(window))
            if previous is not None:
                previous._finish()          # one bar at a time, newest wins
            bar = cls(window, text, on_undo, seconds)
            cls._current[id(window)] = bar
            return bar
        except Exception:
            return None

    @classmethod
    def reposition_all(cls) -> None:
        for bar in list(cls._current.values()):
            try:
                bar._place()
            except Exception:
                continue


class _HoldWhileBusy(QObject):
    """Pauses the bar's countdown while the pointer or the focus is on it."""

    def __init__(self, bar: UndoBar) -> None:
        super().__init__(bar)
        self._bar = bar

    def eventFilter(self, _obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() in (QEvent.Type.Enter, QEvent.Type.FocusIn):
            self._bar.hold()
        elif event.type() in (QEvent.Type.Leave, QEvent.Type.FocusOut):
            self._bar.resume()
        return False


class _Reposition(QObject):
    """Keeps every visible bar at the bottom of its window when it resizes."""

    _instance: "_Reposition | None" = None

    @classmethod
    def get(cls) -> "_Reposition":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def eventFilter(self, _obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize:
            UndoBar.reposition_all()
        return False


def show_undo(widget: QWidget, text: str, on_undo,
              seconds: int = DEFAULT_SECONDS):
    """Module-level shortcut – see UndoBar.show_undo."""
    return UndoBar.show_undo(widget, text, on_undo, seconds)


__all__ = ["UndoBar", "show_undo", "DEFAULT_SECONDS"]

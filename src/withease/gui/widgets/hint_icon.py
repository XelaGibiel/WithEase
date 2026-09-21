"""A small, fixed-size "ⓘ" icon that carries an explanatory tooltip.

Used to move long descriptive text out of the settings UI (visible clutter)
into a hover-triggered tooltip, while keeping a compact, always-present visual
cue that "there is more information here".  A global, app-wide toggle lets
experienced users hide these icons entirely for a cleaner page; every instance
listens for that toggle itself, so callers never have to wire visibility by
hand.

Hovering shows the tip; a CLICK pins it: it stays open - also when the
pointer slips off the icon, which a slight tremor does all the time - until
the icon (or the pinned tip itself) is clicked again.  One tip is pinned at
a time.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from withease.core.event_bus import bus
from withease.core.i18n import tr
from withease.gui.ui_utils import wrap_tooltip

# Module-level (not per-instance) so any file – core or an external module –
# can read/set it without needing an app instance reference, same as `bus`.
_visible: bool = True


def hints_visible() -> bool:
    return _visible


def set_hints_visible(visible: bool) -> None:
    """Update the global hint-visibility state and notify every live
    HintIcon (and any future subscriber) immediately."""
    global _visible
    visible = bool(visible)
    if visible == _visible:
        return
    _visible = visible
    bus.publish("hints.visibility_changed", visible=visible)


class _PinnedTip(QFrame):
    """The explanation of one HintIcon, kept open.  Looks like a tool-tip
    (theme: QFrame#pinnedTip), follows its icon when the page scrolls, and
    closes on a click - on the icon or on itself."""

    def __init__(self, owner: "HintIcon") -> None:
        super().__init__(owner.window(),
                         Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setObjectName("pinnedTip")
        self._owner = owner
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        text = QLabel(owner.pinned_text())
        text.setObjectName("pinnedTipText")
        text.setTextFormat(Qt.TextFormat.RichText)
        # Word wrap on, like Qt's own tool-tip label: only then is the
        # width the tip HTML asks for honoured (ui_utils.wrap_tooltip).
        text.setWordWrap(True)
        layout.addWidget(text)
        foot = QLabel(tr("hint.pinned_close"))
        foot.setObjectName("pinnedTipFoot")
        foot.setWordWrap(True)
        layout.addWidget(foot)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._follow = QTimer(self)
        self._follow.setInterval(100)
        self._follow.timeout.connect(self.place)

    def show_pinned(self) -> None:
        self.adjustSize()
        self.place()
        self.show()
        self._follow.start()

    def place(self) -> None:
        owner = self._owner
        if not owner.isVisible() or not owner.window().isVisible() \
                or owner.window().isMinimized():
            owner.unpin()               # page changed or window gone
            return
        pos = owner.mapToGlobal(QPoint(0, owner.height() + 4))
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = min(max(area.left(), pos.x()), area.right() - self.width())
            y = pos.y()
            if y + self.height() > area.bottom():      # no room below
                y = owner.mapToGlobal(QPoint(0, 0)).y() - self.height() - 4
            pos = QPoint(x, max(area.top(), y))
        if self.pos() != pos:
            self.move(pos)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._owner.unpin()
        event.accept()


class HintIcon(QLabel):
    """A fixed-size "ⓘ" glyph carrying `tooltip` as its hover text.

    Fixed size on purpose (see theme.py QLabel#hintIcon) – like the card
    icons, it must NOT grow with the font-size setting.  Hides itself
    whenever the global hint toggle is off, and re-subscribes/unsubscribes
    cleanly so it never leaks after the widget is destroyed.

    Also reachable by keyboard (Tab) – a plain QLabel takes no focus by
    default, which would make every hint invisible to anyone who can't hover
    a mouse (keyboard- or switch-only use, exactly this app's audience).
    Tabbing to the icon shows the same tooltip a mouse hover would."""

    def __init__(self, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__("ⓘ", parent)
        self.setObjectName("hintIcon")
        self.setToolTip(wrap_tooltip(tooltip))
        # NEVER setVisible(True) here: at this point the icon has no parent
        # yet (label_with_hint() adds it to a layout right after), and showing
        # a parentless widget makes Qt pop it up as a real top-level WINDOW –
        # a tiny "ⓘ" window flashing on screen on every page rebuild (i.e. on
        # every theme/language/font change).  Only hide explicitly; once the
        # icon is in a layout Qt shows it together with its parent.
        if not hints_visible():
            self.setVisible(False)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        # What a screen reader says when it lands here.  Without this it reads
        # out the glyph itself ("circled latin small letter i"), which tells
        # the listener nothing; the description carries the actual sentence,
        # in plain text – the tooltip's HTML table would be read as markup.
        self.setAccessibleName(tr("hint.accessible_name"))
        self.setAccessibleDescription(tooltip)
        self._tip_html = self.toolTip()
        self._pinned: _PinnedTip | None = None
        self.setProperty("pinned", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)   # it can be clicked
        bus.subscribe("hints.visibility_changed", self._on_visibility_changed)
        self.destroyed.connect(
            lambda: bus.unsubscribe(
                "hints.visibility_changed", self._on_visibility_changed))

    def _on_visibility_changed(self, visible: bool, **_: object) -> None:
        if not visible:
            self.unpin()
        self.setVisible(visible)

    # -- pinning ----------------------------------------------------------

    _current: "HintIcon | None" = None       # the one pinned tip, app-wide

    def pinned_text(self) -> str:
        return self._tip_html

    def is_pinned(self) -> bool:
        return self._pinned is not None

    def toggle_pin(self) -> None:
        if self.is_pinned():
            self.unpin()
        else:
            self.pin()

    def pin(self) -> None:
        if self.is_pinned():
            return
        current = HintIcon._current
        if current is not None and current is not self:
            try:
                current.unpin()
            except RuntimeError:        # its widget is already gone
                pass
        QToolTip.hideText()
        # no hover tip on top of the pinned one
        self.setToolTip("")
        self._pinned = _PinnedTip(self)
        self._pinned.show_pinned()
        HintIcon._current = self
        self._set_pinned_look(True)

    def unpin(self) -> None:
        tip, self._pinned = self._pinned, None
        if tip is not None:
            tip.hide()
            tip.deleteLater()
        if HintIcon._current is self:
            HintIcon._current = None
        self.setToolTip(self._tip_html)
        self._set_pinned_look(False)

    def _set_pinned_look(self, pinned: bool) -> None:
        self.setProperty("pinned", pinned)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_pin()
            event.accept()
            return
        super().mousePressEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self.unpin()
        super().hideEvent(event)

    def _show_tip(self) -> None:
        # Same hold as a mouse hover (theme._ToolTipKeeper): the tip stays
        # until focus moves on, instead of timing out mid-sentence.  A NULL
        # rect on purpose – there is no pointer to leave it, and focusOutEvent
        # is what closes the tip again.
        from PySide6.QtCore import QRect

        from withease.gui.theme import _ToolTipKeeper
        QToolTip.showText(self.mapToGlobal(self.rect().bottomLeft()),
                          self.toolTip(), self, QRect(),
                          _ToolTipKeeper.HOLD_MS)

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        if not self.is_pinned():
            self._show_tip()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        QToolTip.hideText()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Escape closes the tip, Space/Enter pins it (and unpins it again) -
        the keyboard way of the click.

        Without this the explanation would cover the controls below it for as
        long as the focus stays here, with no way to look past it – and once
        it had been dismissed there was no way to ask for it again short of
        tabbing away and back."""
        if event.key() == Qt.Key.Key_Escape:
            QToolTip.hideText()
            self.unpin()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                           Qt.Key.Key_Enter):
            QToolTip.hideText()
            self.toggle_pin()
            event.accept()
            return
        super().keyPressEvent(event)

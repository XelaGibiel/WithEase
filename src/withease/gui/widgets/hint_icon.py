"""A small, fixed-size "ⓘ" icon that carries an explanatory tooltip.

Used to move long descriptive text out of the settings UI (visible clutter)
into a hover-triggered tooltip, while keeping a compact, always-present visual
cue that "there is more information here".  A global, app-wide toggle lets
experienced users hide these icons entirely for a cleaner page; every instance
listens for that toggle itself, so callers never have to wire visibility by
hand.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFocusEvent, QKeyEvent
from PySide6.QtWidgets import QLabel, QToolTip, QWidget

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
        bus.subscribe("hints.visibility_changed", self._on_visibility_changed)
        self.destroyed.connect(
            lambda: bus.unsubscribe(
                "hints.visibility_changed", self._on_visibility_changed))

    def _on_visibility_changed(self, visible: bool, **_: object) -> None:
        self.setVisible(visible)

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
        self._show_tip()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        QToolTip.hideText()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Escape closes the tip, Space/Enter brings it back.

        Without this the explanation would cover the controls below it for as
        long as the focus stays here, with no way to look past it – and once
        it had been dismissed there was no way to ask for it again short of
        tabbing away and back."""
        if event.key() == Qt.Key.Key_Escape:
            QToolTip.hideText()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                           Qt.Key.Key_Enter):
            self._show_tip()
            event.accept()
            return
        super().keyPressEvent(event)

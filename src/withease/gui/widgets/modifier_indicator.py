"""Modifier status overlay – shows active Sticky Keys as coloured chips.

Position is configurable (6 screen corners/edges). The widget is only
visible when at least one modifier is latched; it hides automatically
when all are released.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from withease.core.event_bus import bus
from withease.core.i18n import tr

# modifier name → chip colour; the display label is localised via
# tr("key.mod.<name>") at draw time ("Ctrl" → "Strg" in German).
_MODIFIERS: list[tuple[str, str]] = [
    ("shift", "#1565C0"),
    ("ctrl",  "#6A1B9A"),
    ("alt",   "#2E7D32"),
    ("altgr", "#00838F"),
    ("win",   "#E65100"),
]


# Caps Lock is not a Sticky key, but switched on by accident it undoes a
# latched Shift - so it gets a chip of its own, in a warning red.
_CAPSLOCK = ("capslock", "#C62828")
_CAPSLOCK_POLL_MS = 250


def _label(name: str) -> str:
    return tr(f"key.mod.{name}")


def capslock_on() -> bool:
    """Whether Caps Lock is switched on right now (False off Windows)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetKeyState(0x14) & 1)
    except Exception:
        return False

_DEFAULT_CHIP_H = 24

POSITIONS = [
    "top-left", "top-center", "top-right",
    "bottom-left", "bottom-center", "bottom-right",
]


class _Bridge(QObject):
    updated = Signal(dict)
    chip_size_changed = Signal(int)
    preview_changed = Signal(bool)
    capslock_watch_changed = Signal(bool)


class ModifierIndicator(QWidget):
    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._state: dict[str, bool] = {m[0]: False for m in _MODIFIERS}
        self._position = "bottom-right"
        self._chip_h = _DEFAULT_CHIP_H
        self._preview = False
        self._suppressed = False   # hidden over a fullscreen window
        self._capslock_watch = False   # the keyboard module wants it shown
        self._capslock = False         # Caps Lock is on right now
        # Polled: Caps Lock can be switched by any keyboard, by another
        # program or by the on-screen keyboard - no hook sees all of those.
        self._capslock_timer = QTimer(self)
        self._capslock_timer.setInterval(_CAPSLOCK_POLL_MS)
        self._capslock_timer.timeout.connect(self._poll_capslock)

        self._bridge = _Bridge()
        self._bridge.updated.connect(self._apply_state)
        self._bridge.chip_size_changed.connect(self._apply_chip_size)
        self._bridge.preview_changed.connect(self._apply_preview)
        self._bridge.capslock_watch_changed.connect(self._apply_capslock_watch)

        bus.subscribe("keyboard.modifier_status", self._on_status)
        bus.subscribe("keyboard.indicator_position", self._on_position)
        bus.subscribe("keyboard.chip_size", self._on_chip_size)
        bus.subscribe("keyboard.preview", self._on_preview)
        bus.subscribe("keyboard.capslock_watch", self._on_capslock_watch)

        self._update_geometry()
        from withease.gui.widgets.cursor_indicator import IndicatorCoordinator
        IndicatorCoordinator.get().register_suppressible(self)

    # ------------------------------------------------------------------

    def set_position(self, pos: str) -> None:
        if pos in POSITIONS:
            self._position = pos
            self._update_geometry()

    def set_chip_size(self, height: int) -> None:
        self._chip_h = max(16, min(64, height))
        self._update_geometry()
        self.update()

    def _on_status(self, state: dict, **_: object) -> None:
        self._bridge.updated.emit(state)

    def _on_position(self, position: str, **_: object) -> None:
        self.set_position(position)

    def _on_chip_size(self, size: int, **_: object) -> None:
        self._bridge.chip_size_changed.emit(size)

    def _on_preview(self, active: bool, **_: object) -> None:
        self._bridge.preview_changed.emit(active)

    def _on_capslock_watch(self, active: bool, **_: object) -> None:
        self._bridge.capslock_watch_changed.emit(bool(active))

    def _apply_capslock_watch(self, active: bool) -> None:
        self._capslock_watch = active
        if active:
            self._capslock_timer.start()
            self._poll_capslock()
        else:
            self._capslock_timer.stop()
            if self._capslock:
                self._capslock = False
                self._reapply()

    def _poll_capslock(self) -> None:
        on = capslock_on()
        if on != self._capslock:
            self._capslock = on
            if not self._preview:
                self._reapply()

    def _chips(self) -> list[tuple[str, str]]:
        """The chips to draw, left to right: latched keys, then Caps Lock."""
        if self._preview:
            return [*_MODIFIERS, _CAPSLOCK]
        chips = [m for m in _MODIFIERS if self._state.get(m[0])]
        if self._capslock_watch and self._capslock:
            chips.append(_CAPSLOCK)
        return chips

    def _apply_chip_size(self, size: int) -> None:
        self.set_chip_size(size)

    def _should_show(self) -> bool:
        return bool(self._chips())

    def _reapply(self) -> None:
        """Visibility = (preview or a modifier is latched) AND not suppressed
        by a fullscreen window."""
        if self._should_show() and not self._suppressed:
            self._update_geometry()
            self.update()
            self.show()
            # Shown without activating, so Windows keeps the old stacking
            # position – behind whatever was raised in the meantime.  The chip
            # is a status display: it is worthless the moment it is covered.
            self.raise_()
        else:
            self.hide()

    def set_suppressed(self, suppressed: bool) -> None:
        if suppressed != self._suppressed:
            self._suppressed = suppressed
            self._reapply()

    def _apply_preview(self, active: bool) -> None:
        self._preview = active
        self._reapply()

    def _apply_state(self, state: dict) -> None:
        self._state = state
        if self._preview:
            return  # don't hide during preview
        self._reapply()

    # ------------------------------------------------------------------

    def _font_px(self) -> int:
        return max(9, round(self._chip_h * 13 / 24))

    def _width_of(self, name: str) -> int:
        """Modifier chips share one width so they do not jump around; the
        Caps Lock chip is as wide as its longer label needs."""
        if name != _CAPSLOCK[0]:
            return self._chip_w()
        from PySide6.QtGui import QFont, QFontMetrics
        font = QFont()
        font.setPixelSize(self._font_px())
        font.setBold(True)
        text = QFontMetrics(font).horizontalAdvance(_label(name))
        return max(self._chip_w(), text + self._chip_h // 2)

    def _chip_w(self) -> int:
        # Wide enough for the widest LOCALISED label ("Umschalt" is much
        # longer than "Shift"), never narrower than the classic proportion.
        from PySide6.QtGui import QFont, QFontMetrics
        font = QFont()
        font.setPixelSize(self._font_px())
        font.setBold(True)
        fm = QFontMetrics(font)
        widest = max(fm.horizontalAdvance(_label(name))
                     for name, _ in _MODIFIERS)
        return max(round(self._chip_h * 52 / 24), widest + self._chip_h // 2)

    def _gap(self) -> int:
        return round(self._chip_h * 6 / 24)

    def _margin(self) -> int:
        return round(self._chip_h * 12 / 24)

    def _radius(self) -> int:
        return round(self._chip_h * 6 / 24)

    def _update_geometry(self) -> None:
        # at least one chip wide for the initial sizing
        widths = [self._width_of(name) for name, _ in self._chips()] or [
            self._chip_w()]
        gap, margin = self._gap(), self._margin()
        w = sum(widths) + (len(widths) - 1) * gap + 2 * margin
        h = self._chip_h + 2 * margin
        self.setFixedSize(w, h)
        self._reposition()

    def _reposition(self) -> None:
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geom = screen.availableGeometry()  # respects taskbar
        sw, sh = geom.width(), geom.height()
        ox, oy = geom.x(), geom.y()
        ww, wh = self.width(), self.height()

        if "left" in self._position:
            x = ox + self._margin()
        elif "right" in self._position:
            x = ox + sw - ww - self._margin()
        else:
            x = ox + (sw - ww) // 2

        if "top" in self._position:
            y = oy + self._margin()
        else:
            y = oy + sh - wh - self._margin()

        self.move(x, y)

    # ------------------------------------------------------------------

    def paintEvent(self, _event: object) -> None:
        active = self._chips()
        if not active:
            return

        ch = self._chip_h
        gap, margin, radius = self._gap(), self._margin(), self._radius()
        font_px = self._font_px()

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        from PySide6.QtCore import QRect
        x = margin
        for name, colour in active:
            label = _label(name)
            cw = self._width_of(name)
            path = QPainterPath()
            path.addRoundedRect(x, margin, cw, ch, radius, radius)
            p.fillPath(path, QColor(colour))

            p.setPen(QColor("white"))
            font = p.font()
            font.setPixelSize(font_px)
            font.setBold(True)
            p.setFont(font)
            p.drawText(QRect(x, margin, cw, ch), Qt.AlignmentFlag.AlignCenter, label)
            x += cw + gap

        p.end()

"""Shows the free middle of the screen while its size is being set.

"Freier Bereich in der Mitte: 25 %" says little on its own - how big is
that on this screen?  While the slider moves, this overlay draws the area
as a circle around the screen centre; shortly after the slider is let go it
disappears again.  Frameless, click-through and always on top, like the
screen-zone preview.
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from withease.gui import theme

# After the slider is let go (or the last key press on it) the circle stays
# this long - long enough to see where it ended up.
HIDE_AFTER_MS = 1200


class FreeAreaOverlay(QWidget):
    """``show_percent(p)`` shows (or updates) the circle; ``hide_soon()``
    lets it disappear after ``HIDE_AFTER_MS``."""

    def __init__(self) -> None:
        super().__init__(parent=None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._percent = 0
        self._color = QColor(theme.accent())
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(HIDE_AFTER_MS)
        self._hide_timer.timeout.connect(self.hide)

    def show_percent(self, percent: int, near: QWidget | None = None) -> None:
        """Show the circle for ``percent`` of the screen height, on the
        screen the settings window is on."""
        self._hide_timer.stop()
        self._percent = max(0, int(percent))
        screen = None
        if near is not None:
            screen = near.screen()
        screen = screen or QApplication.primaryScreen()
        if screen is not None and self.geometry() != screen.geometry():
            self.setGeometry(screen.geometry())
        if not self.isVisible():
            self.show()
            self._make_click_through()
        self.update()

    def hide_soon(self) -> None:
        if self.isVisible():
            self._hide_timer.start()

    def radius(self) -> float:
        return self.height() * self._percent / 100.0

    def _make_click_through(self) -> None:
        if sys.platform != "win32":
            return
        try:
            gwl_exstyle, ws_ex_layered, ws_ex_transparent = -20, 0x80000, 0x20
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, gwl_exstyle)
            user32.SetWindowLongW(hwnd, gwl_exstyle,
                                  style | ws_ex_layered | ws_ex_transparent)
        except Exception:
            pass

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        centre = QPointF(self.width() / 2, self.height() / 2)
        r = self.radius()
        fill = QColor(self._color)
        fill.setAlpha(45)
        p.setBrush(fill)
        pen = QPen(self._color, 3)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawEllipse(centre, max(r, 4.0), max(r, 4.0))
        # the value in the middle, readable on any background
        text = f"{self._percent} %"
        font = p.font()
        font.setPointSizeF(max(14.0, font.pointSizeF() * 1.6))
        font.setBold(True)
        p.setFont(font)
        box = QRectF(centre.x() - 80, centre.y() - 24, 160, 48)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(box, 10, 10)
        p.setPen(QColor(255, 255, 255))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        p.end()

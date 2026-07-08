"""Full-screen overlay that visualises the screen-zone grid."""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget


class ScreenZoneOverlay(QWidget):
    """Frameless, click-through, always-on-top window that draws the zone grid."""

    def __init__(self, rows: int = 3, cols: int = 3) -> None:
        super().__init__(parent=None)
        self._rows = rows
        self._cols = cols
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        screen = QApplication.primaryScreen()
        self.setGeometry(screen.geometry())

    def showEvent(self, event: object) -> None:  # type: ignore[override]
        super().showEvent(event)  # type: ignore[arg-type]
        self._make_click_through()

    def _make_click_through(self) -> None:
        """Set WS_EX_TRANSPARENT | WS_EX_LAYERED so all mouse events pass through."""
        if sys.platform != "win32":
            return
        try:
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                  ex_style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        except Exception:
            pass

    # ------------------------------------------------------------------

    def paintEvent(self, _event: object) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rows, cols = self._rows, self._cols
        w, h = self.width(), self.height()
        cw, ch = w // cols, h // rows

        # Alternating cell tint
        for row in range(rows):
            for col in range(cols):
                alpha = 35 if (row + col) % 2 == 0 else 18
                painter.fillRect(col * cw, row * ch, cw, ch, QColor(30, 100, 220, alpha))

        # Grid lines
        pen = QPen(QColor(60, 130, 255, 210))
        pen.setWidth(2)
        painter.setPen(pen)
        for i in range(1, cols):
            painter.drawLine(i * cw, 0, i * cw, h)
        for i in range(1, rows):
            painter.drawLine(0, i * ch, w, i * ch)
        painter.drawRect(1, 1, w - 2, h - 2)

        # Zone number labels (1-indexed, left-to-right, top-to-bottom)
        font = QFont()
        font.setPointSize(max(24, h // 25))
        font.setBold(True)
        painter.setFont(font)

        zone_num = 1
        for row in range(rows):
            for col in range(cols):
                rect_shadow = QRect(col * cw + 2, row * ch + 2, cw, ch)
                rect_main   = QRect(col * cw,     row * ch,     cw, ch)
                painter.setPen(QColor(0, 0, 0, 120))
                painter.drawText(rect_shadow, Qt.AlignmentFlag.AlignCenter, str(zone_num))
                painter.setPen(QColor(220, 235, 255, 230))
                painter.drawText(rect_main, Qt.AlignmentFlag.AlignCenter, str(zone_num))
                zone_num += 1

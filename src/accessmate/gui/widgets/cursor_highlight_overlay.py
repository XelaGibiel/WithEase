"""Cursor highlight overlay – pulsing rings to quickly locate the cursor.

Triggered via hotkey.  Draws several concentric rings that expand and fade
out around the current cursor position for a short time, making it easy to
spot where the pointer is.  The overlay is full-screen, always-on-top and
click-through so it never interferes with normal interaction.
"""
from __future__ import annotations

import ctypes
import sys

import math

from PySide6.QtCore import QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget

from accessmate.core.event_bus import bus

# Animation parameters
_DURATION_MS = 1600      # default total time the highlight is visible
_FRAME_MS = 16           # ~60 fps
_MAX_RADIUS = 90         # outermost ring radius in pixels
_PULSE_MS = 900          # expansion time of ONE ring – constant pulse speed
_STAGGER_MS = 290        # interval between successive ring launches

# Direction arrow
_ARROW_COLOR = (255, 215, 0)   # yellow
_ARROW_MIN_DIST = 120          # don't show arrow if cursor is closer than this
_ARROW_THICKNESS = 6           # default shaft width in pixels


_DEFAULT_COLOR = (255, 140, 0)   # orange, matches app accent


class _Bridge(QObject):
    """Relays bus events from any thread to the Qt main thread."""
    trigger = Signal(bool, object, int, bool, int, int, str)


class CursorHighlightOverlay(QWidget):
    """Full-screen, click-through overlay that pulses rings around the cursor."""

    def __init__(self) -> None:
        super().__init__(parent=None)
        self._rings = True
        self._ring_style = "open"   # "open" (logo-style gap) | "closed"
        self._color = _DEFAULT_COLOR
        self._max_radius = _MAX_RADIUS
        self._arrow = False
        self._arrow_thickness = _ARROW_THICKNESS
        self._duration_ms = _DURATION_MS
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._elapsed = 0
        self._center = (0, 0)

        self._anim = QTimer(self)
        self._anim.setInterval(_FRAME_MS)
        self._anim.timeout.connect(self._on_frame)

        self._bridge = _Bridge()
        self._bridge.trigger.connect(self._start)
        bus.subscribe("mouse.highlight", self._on_highlight)

    # ------------------------------------------------------------------

    def _on_highlight(self, rings: bool = True, color: object = None,
                      radius: int = 0, arrow: bool = False,
                      arrow_thickness: int = 0, duration_ms: int = 0,
                      ring_style: str = "open", **_: object) -> None:
        self._bridge.trigger.emit(rings, color, radius, arrow,
                                  arrow_thickness, duration_ms,
                                  ring_style or "open")

    def _start(self, rings: bool, color: object, radius: int, arrow: bool,
               arrow_thickness: int, duration_ms: int,
               ring_style: str = "open") -> None:
        """Position over the active screen and (re)start the pulse animation."""
        self._rings = bool(rings)
        self._ring_style = ring_style if ring_style in ("open", "closed") \
            else "open"
        if isinstance(color, (tuple, list)) and len(color) == 3:
            self._color = tuple(int(c) for c in color)
        else:
            self._color = _DEFAULT_COLOR
        self._max_radius = radius if radius and radius > 0 else _MAX_RADIUS
        self._arrow = bool(arrow)
        self._arrow_thickness = (
            arrow_thickness if arrow_thickness and arrow_thickness > 0
            else _ARROW_THICKNESS)
        self._duration_ms = (duration_ms if duration_ms and duration_ms > 0
                             else _DURATION_MS)

        pos = QCursor.pos()
        self._center = (pos.x(), pos.y())

        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        if screen is None:
            return
        self.setGeometry(screen.geometry())

        self._elapsed = 0
        if not self.isVisible():
            self.show()
        self._make_click_through()
        self._anim.start()
        self.update()

    def _on_frame(self) -> None:
        self._elapsed += _FRAME_MS
        if self._elapsed >= self._duration_ms:
            self._anim.stop()
            self.hide()
            return
        # Follow the cursor while the pulse is running, so the rings stay
        # attached to the pointer instead of the position at trigger time.
        pos = QCursor.pos()
        self._center = (pos.x(), pos.y())
        if not self.geometry().contains(pos):
            # Cursor moved to another screen – move the overlay along.
            screen = QApplication.screenAt(pos)
            if screen is not None:
                self.setGeometry(screen.geometry())
                self._make_click_through()
        self.update()

    def _make_click_through(self) -> None:
        """Set WS_EX_TRANSPARENT | WS_EX_LAYERED so mouse events pass through."""
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
        if self._elapsed >= self._duration_ms:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Cursor position is global; convert to widget-local coordinates.
        cx = self._center[0] - self.x()
        cy = self._center[1] - self.y()

        progress = self._elapsed / self._duration_ms  # 0 → 1
        r, g, b = self._color

        if self._rings:
            # Rings expand at a CONSTANT speed (_PULSE_MS per ring); a longer
            # total duration simply launches more rings, one every
            # _STAGGER_MS, timed so the last one finishes exactly on time.
            ring_count = max(1, (self._duration_ms - _PULSE_MS) // _STAGGER_MS + 1)
            for i in range(int(ring_count)):
                phase = (self._elapsed - i * _STAGGER_MS) / _PULSE_MS
                if phase < 0 or phase > 1:
                    continue
                radius = phase * self._max_radius
                alpha = int(220 * (1.0 - phase))  # fade out as it expands
                if alpha <= 0:
                    continue
                pen = QPen(QColor(r, g, b, alpha))
                pen.setWidth(4)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                d, x0, y0 = int(radius * 2), int(cx - radius), int(cy - radius)
                if self._ring_style == "open":
                    # Open ring like the AccessMate logo: ~300° arc with a gap
                    # toward the upper right (Qt angles: 0°=east, CCW, 1/16°).
                    painter.drawArc(x0, y0, d, d, 75 * 16, 300 * 16)
                else:
                    painter.drawEllipse(x0, y0, d, d)

            # Solid centre dot, fading over the first ring's pulse.
            dot_alpha = int(230 * max(0.0, 1.0 - self._elapsed / _PULSE_MS * 1.6))
            if dot_alpha > 0:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(r, g, b, dot_alpha))
                painter.drawEllipse(cx - 6, cy - 6, 12, 12)

        # Direction arrow from screen centre toward the cursor.
        if self._arrow:
            self._draw_arrow(painter, cx, cy, progress)

        painter.end()

    def _draw_arrow(self, painter: QPainter, cx: int, cy: int,
                    progress: float) -> None:
        """Draw a solid triangular pointer at the screen centre.

        The triangle points toward the cursor.  It has no shaft – just a clean
        filled arrowhead whose size scales with the configured thickness.
        """
        scx = self.width() / 2
        scy = self.height() / 2
        dx = cx - scx
        dy = cy - scy
        dist = math.hypot(dx, dy)
        if dist < _ARROW_MIN_DIST:
            return  # cursor already near centre – arrow not helpful

        angle = math.atan2(dy, dx)
        alpha = int(235 * (1.0 - progress))
        if alpha <= 0:
            return
        ar, ag, ab = _ARROW_COLOR

        # Triangle geometry scales with thickness.  A long, slender shape
        # makes the pointing direction obvious (avoids an equilateral "blob").
        length = self._arrow_thickness * 9.0    # tip-to-base distance
        half_w = self._arrow_thickness * 2.6    # half of base width

        # Tip sits a fixed distance out from centre, pointing at the cursor.
        base_center_dist = 24
        tip = QPointF(scx + math.cos(angle) * (base_center_dist + length),
                      scy + math.sin(angle) * (base_center_dist + length))
        base = QPointF(scx + math.cos(angle) * base_center_dist,
                       scy + math.sin(angle) * base_center_dist)

        # Perpendicular direction for the base corners.
        perp = angle + math.pi / 2
        left = QPointF(base.x() + math.cos(perp) * half_w,
                       base.y() + math.sin(perp) * half_w)
        right = QPointF(base.x() - math.cos(perp) * half_w,
                        base.y() - math.sin(perp) * half_w)

        # Concave notch in the rear so it reads as a proper arrowhead/chevron.
        notch = QPointF(base.x() + math.cos(angle) * (length * 0.35),
                        base.y() + math.sin(angle) * (length * 0.35))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ar, ag, ab, alpha))
        painter.drawPolygon(QPolygonF([tip, left, notch, right]))

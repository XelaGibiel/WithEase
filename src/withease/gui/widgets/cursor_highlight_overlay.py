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

from withease.core.event_bus import bus

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
                    # Open ring like the WithEase logo: ~300° arc with the gap
                    # toward the upper LEFT.  Qt angles: 0°=east, 90°=top, CCW,
                    # in 1/16°; gap centred at 135° → draw 165°…465°(=105°).
                    painter.drawArc(x0, y0, d, d, 165 * 16, 300 * 16)
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


def _apply_click_through(widget: QWidget) -> None:
    """Make a window transparent to mouse input (WS_EX_TRANSPARENT|LAYERED)."""
    if sys.platform != "win32":
        return
    try:
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        hwnd = int(widget.winId())
        user32 = ctypes.windll.user32
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                              ex | WS_EX_LAYERED | WS_EX_TRANSPARENT)
    except Exception:
        pass


class _ArrowBridge(QObject):
    config = Signal(bool, str, int, object)   # enabled, corner, size, color


class DirectionArrowOverlay(QWidget):
    """A permanent arrow anchored in a screen corner that always points at the
    cursor.

    Unlike the pulsing highlight, this stays visible while enabled.  The corner
    (top-left / top-right / bottom-left / bottom-right) and the size are
    configurable.  The overlay is full-screen, always-on-top and click-through.
    """

    _CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")

    def __init__(self) -> None:
        super().__init__(parent=None)
        self._enabled = False
        self._corner = "bottom-right"
        self._size = 48
        self._color = _DEFAULT_COLOR
        self._last_cursor = (-1, -1)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Follow the cursor so the arrow keeps pointing at it (~30 fps).
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._on_tick)

        self._bridge = _ArrowBridge()
        self._bridge.config.connect(self._apply_config)
        bus.subscribe("mouse.direction_arrow", self._on_config)

    # ------------------------------------------------------------------

    def _on_config(self, enabled: bool = False, corner: str = "bottom-right",
                   size: int = 48, color: object = None, **_: object) -> None:
        self._bridge.config.emit(bool(enabled), str(corner), int(size), color)

    def _apply_config(self, enabled: bool, corner: str, size: int,
                      color: object) -> None:
        self._enabled = enabled
        self._corner = corner if corner in self._CORNERS else "bottom-right"
        self._size = size if size and size > 0 else 48
        if isinstance(color, (tuple, list)) and len(color) == 3:
            self._color = tuple(int(c) for c in color)
        else:
            self._color = _DEFAULT_COLOR

        if not enabled:
            self._timer.stop()
            self.hide()
            return

        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        if not self.isVisible():
            self.show()
        _apply_click_through(self)
        self.raise_()
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def _on_tick(self) -> None:
        pos = QCursor.pos()
        if (pos.x(), pos.y()) != self._last_cursor:
            self._last_cursor = (pos.x(), pos.y())
            self.update()

    def paintEvent(self, _event: object) -> None:  # type: ignore[override]
        if not self._enabled:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        margin = max(30.0, self._size * 0.95)
        w, h = self.width(), self.height()
        ax = margin if "left" in self._corner else w - margin
        ay = margin if "top" in self._corner else h - margin

        pos = QCursor.pos()
        dx = (pos.x() - self.x()) - ax
        dy = (pos.y() - self.y()) - ay
        if math.hypot(dx, dy) < 1:
            painter.end()
            return
        angle = math.degrees(math.atan2(dy, dx))

        r, g, b = self._color
        length = float(self._size)
        half_w = self._size * 0.72 / 2
        tip = QPointF(length * 0.55, 0)
        base_left = QPointF(-length * 0.45, half_w)
        notch = QPointF(-length * 0.18, 0)
        base_right = QPointF(-length * 0.45, -half_w)

        painter.translate(ax, ay)
        painter.rotate(angle)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(r, g, b, 235))
        painter.drawPolygon(QPolygonF([tip, base_left, notch, base_right]))
        painter.end()


class _SpotlightBridge(QObject):
    config = Signal(bool, object, int, int)   # enabled, color, radius, opacity


class CursorSpotlightOverlay(QWidget):
    """A permanent, lightly translucent filled circle that follows the cursor.

    Unlike the pulsing highlight (which appears briefly on a hotkey), this stays
    visible the whole time it is enabled, so the pointer is always easy to spot
    – a soft coloured disc under it.  Full-screen, always-on-top, click-through.
    """

    def __init__(self) -> None:
        super().__init__(parent=None)
        self._enabled = False
        self._color = _DEFAULT_COLOR
        self._radius = 40
        self._opacity = 25          # percent of full colour opacity
        self._center = (0, 0)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Follow the cursor smoothly (~60 fps) so the disc stays under it.
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._on_tick)

        self._bridge = _SpotlightBridge()
        self._bridge.config.connect(self._apply_config)
        bus.subscribe("mouse.cursor_spotlight", self._on_config)

    def _on_config(self, enabled: bool = False, color: object = None,
                   radius: int = 40, opacity: int = 25, **_: object) -> None:
        self._bridge.config.emit(bool(enabled), color, int(radius), int(opacity))

    def _apply_config(self, enabled: bool, color: object, radius: int,
                      opacity: int) -> None:
        self._enabled = enabled
        if isinstance(color, (tuple, list)) and len(color) == 3:
            self._color = tuple(int(c) for c in color)
        else:
            self._color = _DEFAULT_COLOR
        self._radius = radius if radius and radius > 0 else 40
        self._opacity = max(5, min(90, opacity)) if opacity else 25

        if not enabled:
            self._timer.stop()
            self.hide()
            return

        pos = QCursor.pos()
        self._center = (pos.x(), pos.y())
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        if not self.isVisible():
            self.show()
        _apply_click_through(self)
        self.raise_()
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def _on_tick(self) -> None:
        pos = QCursor.pos()
        if (pos.x(), pos.y()) == self._center:
            return
        self._center = (pos.x(), pos.y())
        if not self.geometry().contains(pos):
            screen = QApplication.screenAt(pos)
            if screen is not None:
                self.setGeometry(screen.geometry())
                _apply_click_through(self)
        self.update()

    def paintEvent(self, _event: object) -> None:  # type: ignore[override]
        if not self._enabled:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self._center[0] - self.x()
        cy = self._center[1] - self.y()
        r, g, b = self._color
        d, x0, y0 = self._radius * 2, cx - self._radius, cy - self._radius
        # Soft filled disc.
        fill = int(255 * self._opacity / 100)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(r, g, b, fill))
        painter.drawEllipse(x0, y0, d, d)
        # A slightly stronger outline so the edge stays defined at low opacity.
        pen = QPen(QColor(r, g, b, min(255, fill + 70)))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(x0, y0, d, d)
        painter.end()

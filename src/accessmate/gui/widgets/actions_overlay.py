"""Favorites overlay – always-visible list of favourite actions and their keys.

A small always-on-top panel pinned to a screen edge (or freely dragged by the
user) showing the hotkeys of the actions/macros the user marked as favourites.
Optional hover-hide: when the mouse touches the panel it fades away so the
window behind it can be reached, and comes back once the cursor moves off.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QRect,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from accessmate.core.event_bus import bus

if TYPE_CHECKING:
    from accessmate.app import AccessMateApp

_BG = QColor(30, 34, 42, 235)
_BORDER = QColor(230, 81, 0, 200)
_LABEL_FG = QColor(235, 238, 245)
_KEY_FG = QColor(255, 176, 110)
_RADIUS = 8
_PAD = 10
_DEFAULT_FONT_PX = 12
_MARGIN = 12
_FADE_MS = 250

POSITIONS = [
    "top-left", "top-center", "top-right",
    "bottom-left", "bottom-center", "bottom-right",
    "custom",
]


class _Bridge(QObject):
    refresh = Signal()


class ActionsOverlay(QWidget):
    def __init__(self, app: "AccessMateApp") -> None:
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._app = app
        self._rows: list[tuple[str, str]] = []
        self._dragging = False
        self._drag_offset = QPoint()
        self._hover_hidden = False
        self._font_px = _DEFAULT_FONT_PX

        # While hover-hidden there is no leaveEvent (widget is invisible), so
        # poll the cursor until it moves away, then show again.
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(150)
        self._hover_timer.timeout.connect(self._check_hover_return)

        # Smooth fade for hover-hide/show.
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(_FADE_MS)
        self._fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade.valueChanged.connect(
            lambda v: self.setWindowOpacity(float(v)))

        self._bridge = _Bridge()
        self._bridge.refresh.connect(self.refresh)
        for event in ("profiles.changed", "module.settings_changed",
                      "overlay.config_changed", "module.started",
                      "module.stopped"):
            bus.subscribe(event, lambda **_: self._bridge.refresh.emit())

        self.refresh()

    # ------------------------------------------------------------------

    def _config(self) -> dict:
        return self._app.get_overlay_config()

    def refresh(self) -> None:
        """Re-read favourites + config and show/hide/reposition accordingly."""
        cfg = self._config()
        self._rows = self._app.get_favorite_rows()
        if not cfg.get("enabled", False) or not self._rows:
            self._hover_timer.stop()
            self._hover_hidden = False
            self.hide()
            return
        self._font_px = max(8, min(32, int(cfg.get("font_size",
                                                   _DEFAULT_FONT_PX))))
        self._resize_to_content()
        self._reposition(cfg)
        if not self._hover_hidden:
            self.setWindowOpacity(1.0)
            self.show()
        self.update()

    def _row_h(self) -> int:
        return self._font_px + 10

    _COL_GAP = 14

    def _column_widths(self) -> tuple[int, int]:
        from PySide6.QtGui import QFont, QFontMetrics
        fm = self.fontMetrics()
        label_w = max((fm.horizontalAdvance(label) for label, _ in self._rows),
                      default=0)
        # Keys are painted BOLD – measure them with the bold variant, or the
        # last characters get clipped.
        bold = QFont(self.font())
        bold.setBold(True)
        fm_bold = QFontMetrics(bold)
        key_w = max((fm_bold.horizontalAdvance(key) for _, key in self._rows),
                    default=0)
        return label_w, key_w + 4  # small safety margin

    def _resize_to_content(self) -> None:
        font = self.font()
        font.setPixelSize(self._font_px)
        self.setFont(font)
        label_w, key_w = self._column_widths()
        w = min(max(140, label_w + self._COL_GAP + key_w + 2 * _PAD), 720)
        h = len(self._rows) * self._row_h() + 2 * _PAD
        self.setFixedSize(w, h)

    def _reposition(self, cfg: dict) -> None:
        pos = cfg.get("position", "bottom-right")
        if pos == "custom":
            custom = cfg.get("custom_pos")
            if custom:
                self.move(int(custom[0]), int(custom[1]))
                return
            pos = "bottom-right"
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geom = screen.availableGeometry()
        if "left" in pos:
            x = geom.x() + _MARGIN
        elif "right" in pos:
            x = geom.x() + geom.width() - self.width() - _MARGIN
        else:
            x = geom.x() + (geom.width() - self.width()) // 2
        y = (geom.y() + _MARGIN if "top" in pos
             else geom.y() + geom.height() - self.height() - _MARGIN)
        self.move(x, y)

    # ------------------------------------------------------------------
    # Hover hide
    # ------------------------------------------------------------------

    def enterEvent(self, event: object) -> None:  # type: ignore[override]
        if (self._config().get("hover_hide", False) and not self._dragging
                and not self._hover_hidden):
            self._hover_hidden = True
            self._fade.stop()
            try:
                self._fade.finished.disconnect()
            except RuntimeError:
                pass
            self._fade.setStartValue(self.windowOpacity())
            self._fade.setEndValue(0.0)
            self._fade.finished.connect(self._on_fade_out_done)
            self._fade.start()
        super().enterEvent(event)  # type: ignore[arg-type]

    def _on_fade_out_done(self) -> None:
        try:
            self._fade.finished.disconnect()
        except RuntimeError:
            pass
        if self._hover_hidden:
            self.hide()
            self._hover_timer.start()

    def _check_hover_return(self) -> None:
        # Reappear once the cursor left the panel area (plus a small margin).
        area = self.geometry().adjusted(-24, -24, 24, 24)
        if not area.contains(QCursor.pos()):
            self._hover_timer.stop()
            self._hover_hidden = False
            if self._config().get("enabled", False) and self._rows:
                self._fade.stop()
                try:
                    self._fade.finished.disconnect()
                except RuntimeError:
                    pass
                self.setWindowOpacity(0.0)
                self.show()
                self._fade.setStartValue(0.0)
                self._fade.setEndValue(1.0)
                self._fade.start()

    # ------------------------------------------------------------------
    # Dragging
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._dragging:
            self._dragging = False
            self._app.set_overlay_custom_pos(self.x(), self.y())

    # ------------------------------------------------------------------

    def paintEvent(self, _event: object) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), _RADIUS, _RADIUS)
        p.fillPath(path, _BG)
        p.setPen(_BORDER)
        p.drawPath(path)

        font = p.font()
        font.setPixelSize(self._font_px)
        row_h = self._row_h()
        # Two aligned columns: labels right-aligned against a common edge,
        # hotkeys left-aligned right next to them.
        label_w, _key_w = self._column_widths()
        key_x = _PAD + label_w + self._COL_GAP
        y = _PAD
        for label, key in self._rows:
            font.setBold(False)
            p.setFont(font)
            p.setPen(_LABEL_FG)
            p.drawText(QRect(_PAD, y, label_w, row_h),
                       Qt.AlignmentFlag.AlignVCenter
                       | Qt.AlignmentFlag.AlignRight, label)
            font.setBold(True)
            p.setFont(font)
            p.setPen(_KEY_FG)
            p.drawText(QRect(key_x, y, self.width() - key_x - _PAD, row_h),
                       Qt.AlignmentFlag.AlignVCenter, key)
            y += row_h
        p.end()

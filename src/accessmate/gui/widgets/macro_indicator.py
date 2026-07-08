"""Macro mode indicator – visible overlay while macro mode is active."""
from __future__ import annotations

from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from accessmate.core.event_bus import bus
from accessmate.core.i18n import tr

_BG     = "#E65100"
_FG     = "#FFFFFF"
_RADIUS = 6
_MARGIN = 12
_DEFAULT_H = 28


class _Bridge(QObject):
    changed  = Signal(bool)
    resized  = Signal(int)
    preview  = Signal(bool)


class MacroModeIndicator(QWidget):
    """Frameless overlay shown while macro mode is active."""

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

        self._chip_h = _DEFAULT_H
        self._update_size()

        self._preview = False
        self._active = False       # macro mode currently on
        self._suppressed = False   # hidden over a fullscreen window

        self._bridge = _Bridge()
        self._bridge.changed.connect(self._apply)
        self._bridge.resized.connect(self._on_resize)
        self._bridge.preview.connect(self._on_preview)

        bus.subscribe("macros.mode_changed", self._on_changed)
        bus.subscribe("macros.chip_size",    self._on_chip_size)
        bus.subscribe("macros.preview",      self._on_preview_event)
        self._reposition()
        from accessmate.gui.widgets.cursor_indicator import IndicatorCoordinator
        IndicatorCoordinator.get().register_suppressible(self)

    # ------------------------------------------------------------------

    def _chip_w(self) -> int:
        return max(120, int(self._chip_h * 5.5))

    def _update_size(self) -> None:
        self.setFixedSize(self._chip_w() + 2 * _MARGIN, self._chip_h + 2 * _MARGIN)

    def set_chip_size(self, height: int) -> None:
        self._chip_h = max(16, min(64, height))
        self._update_size()
        self._reposition()
        self.update()

    def _on_chip_size(self, size: int, **_: object) -> None:
        self._bridge.resized.emit(size)

    def _on_resize(self, size: int) -> None:
        self.set_chip_size(size)
        if self._preview or self.isVisible():
            self._reposition()

    def _on_preview_event(self, active: bool, **_: object) -> None:
        self._bridge.preview.emit(active)

    def _on_preview(self, active: bool) -> None:
        self._preview = active
        self._reapply()

    # ------------------------------------------------------------------

    def _on_changed(self, active: bool, **_: object) -> None:
        self._bridge.changed.emit(active)

    def _apply(self, active: bool) -> None:
        self._active = active
        self._reapply()

    def _should_show(self) -> bool:
        return self._active or self._preview

    def _reapply(self) -> None:
        """Visibility = (macro mode on or preview) AND not suppressed by a
        fullscreen window."""
        if self._should_show() and not self._suppressed:
            self._reposition()
            self.show()
        else:
            self.hide()

    def set_suppressed(self, suppressed: bool) -> None:
        if suppressed != self._suppressed:
            self._suppressed = suppressed
            self._reapply()

    def _reposition(self) -> None:
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geom = screen.availableGeometry()
        x = geom.x() + (geom.width() - self.width()) // 2
        y = geom.y() + _MARGIN
        self.move(x, y)

    # ------------------------------------------------------------------

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(_MARGIN, _MARGIN, self._chip_w(), self._chip_h, _RADIUS, _RADIUS)
        p.fillPath(path, QColor(_BG))

        p.setPen(QColor(_FG))
        font = p.font()
        font.setPixelSize(max(10, int(self._chip_h * 0.5)))
        font.setBold(True)
        p.setFont(font)

        p.drawText(
            QRect(_MARGIN, _MARGIN, self._chip_w(), self._chip_h),
            Qt.AlignmentFlag.AlignCenter,
            tr("module.macros.indicator_label"),
        )
        p.end()

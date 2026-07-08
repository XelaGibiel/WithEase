"""Reusable cursor-following indicator overlays.

Each indicator shows a symbol next to the mouse cursor while a feature is active.
All active indicators are positioned side by side by IndicatorCoordinator.

Symbols in use:
  🔒  click_lock_indicator.py   – Click-Lock
  🎯  CenteringIndicator        – Cursor centering (countdown + snap)
  🐌  PrecisionIndicator        – Precision mode
"""
from __future__ import annotations

import ctypes

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import QWidget

from accessmate.core.event_bus import bus

_SIZE = 28
_GAP = 2


def _cursor_offset() -> int:
    """Offset in pixels that clears the current system cursor."""
    try:
        size = ctypes.windll.user32.GetSystemMetrics(13)  # SM_CXCURSOR
        return max(24, size + 4)
    except Exception:
        return 32


# ---------------------------------------------------------------------------
# Coordinator – keeps all registered indicators side by side
# ---------------------------------------------------------------------------

class IndicatorCoordinator:
    """Singleton that repositions all visible indicators next to the cursor."""

    _instance: IndicatorCoordinator | None = None

    def __init__(self) -> None:
        self._indicators: list[CursorIndicator] = []
        self._timer = QTimer()
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._reposition)
        self._timer.start()

    @classmethod
    def get(cls) -> IndicatorCoordinator:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, indicator: CursorIndicator) -> None:
        self._indicators.append(indicator)

    def _reposition(self) -> None:
        visible = [i for i in self._indicators if i.isVisible()]
        if not visible:
            return
        offset = _cursor_offset()
        pos = QCursor.pos()
        x = pos.x() + offset
        y = pos.y() + offset
        for ind in visible:
            ind.move(x, y)
            x += _SIZE + _GAP


# ---------------------------------------------------------------------------
# Base widget
# ---------------------------------------------------------------------------

class _Bridge(QObject):
    """Relays bus events from any thread to the main thread via Qt signals."""
    show_requested = Signal()
    hide_requested = Signal()
    config_changed = Signal(bool)
    pulse_start = Signal()
    pulse_stop = Signal()
    countdown = Signal()
    centered = Signal()
    aborted = Signal()


class CursorIndicator(QWidget):
    """Small always-on-top symbol that follows the cursor.

    ``config_key`` links the indicator to the ``mouse.indicator_config``
    event: the user can hide any single cursor symbol (target/snail/lock)
    while keeping the underlying feature active.
    """

    def __init__(self, symbol: str, size: int = _SIZE,
                 config_key: str | None = None) -> None:
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(size, size)
        self._symbol = symbol
        self._font_size = size - 4
        self._show_enabled = True
        self._config_key = config_key

        self._bridge = _Bridge()
        self._bridge.show_requested.connect(self._do_show)
        self._bridge.hide_requested.connect(self._do_hide)
        self._bridge.config_changed.connect(self._apply_config)

        # Force the native window handle to be created up front.  On a cold
        # autostart the very first show() of a translucent always-on-top
        # window can silently fail until the shell is fully up; pre-creating
        # the handle here makes the first real show() reliable.
        self.winId()

        if config_key is not None:
            bus.subscribe("mouse.indicator_config", self._on_config)

        IndicatorCoordinator.get().register(self)

    # Thread-safe API -------------------------------------------------------

    def show_indicator(self) -> None:
        self._bridge.show_requested.emit()

    def hide_indicator(self) -> None:
        self._bridge.hide_requested.emit()

    # Config (show/hide this symbol) ---------------------------------------

    def _on_config(self, **flags: object) -> None:
        self._bridge.config_changed.emit(bool(flags.get(self._config_key, True)))

    def _apply_config(self, enabled: bool) -> None:
        self._show_enabled = enabled
        if not enabled and self.isVisible():
            self.hide()

    def _guarded_show(self) -> None:
        """show(), unless the user turned this symbol off."""
        if self._show_enabled:
            self.show()

    # Main-thread slots -----------------------------------------------------

    def _do_show(self) -> None:
        self._guarded_show()

    def _do_hide(self) -> None:
        self.hide()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = p.font()
        font.setPixelSize(self._font_size)
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._symbol)
        p.end()

    def set_symbol(self, symbol: str) -> None:
        self._symbol = symbol
        if self.isVisible():
            self.update()


# ---------------------------------------------------------------------------
# Concrete indicators
# ---------------------------------------------------------------------------

class CenteringIndicator(CursorIndicator):
    """Shows 🎯 during the centering countdown and briefly after snapping.

    The target symbol pulses (fades in and out) while the countdown runs, so
    it draws the eye and signals that centering is imminent.
    """

    _SYMBOL = "\U0001F3AF"   # 🎯
    _PULSE_MS = 40           # animation frame interval
    _PULSE_PERIOD_MS = 900   # one full fade cycle

    def __init__(self) -> None:
        super().__init__(self._SYMBOL, config_key="centering")

        # Pulse animation (opacity oscillation) during the countdown.
        # Timer ops must happen on the Qt main thread, so we go via the bridge.
        self._pulse_opacity = 1.0
        self._pulse_elapsed = 0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(self._PULSE_MS)
        self._pulse_timer.timeout.connect(self._on_pulse)
        self._bridge.pulse_start.connect(self._start_pulse)
        self._bridge.pulse_stop.connect(self._stop_pulse)
        # All QTimer ops must run on the Qt main thread – bus events arrive on
        # the mouse module's worker threads, so marshal via these signals.
        self._bridge.countdown.connect(self._show_countdown)
        self._bridge.centered.connect(self._show_centered)
        self._bridge.aborted.connect(self._do_aborted)

        bus.subscribe("mouse.centering_countdown", self._on_countdown)
        bus.subscribe("mouse.centered",            self._on_centered)
        bus.subscribe("mouse.centering_aborted",   lambda **_: self._on_aborted())

    def _start_pulse(self) -> None:
        self._pulse_elapsed = 0
        if not self._pulse_timer.isActive():
            self._pulse_timer.start()

    def _stop_pulse(self) -> None:
        self._pulse_timer.stop()
        self._pulse_opacity = 1.0
        self.update()

    def _on_pulse(self) -> None:
        import math
        self._pulse_elapsed += self._PULSE_MS
        phase = (self._pulse_elapsed % self._PULSE_PERIOD_MS) / self._PULSE_PERIOD_MS
        # Full fade 1.0 → 0.0 → 1.0, starting at full opacity so the first
        # pulse eases in smoothly instead of jumping from full to invisible.
        self._pulse_opacity = 0.5 + 0.5 * math.cos(phase * 2 * math.pi)
        self.update()

    def paintEvent(self, event: object) -> None:  # type: ignore[override]
        from PySide6.QtGui import QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._pulse_opacity)
        font = p.font()
        font.setPixelSize(self._font_size)
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._symbol)
        p.end()

    # Bus-thread entry points – just relay to the main thread ---------------

    def _on_countdown(self, seconds: int, **_: object) -> None:
        self._bridge.countdown.emit()

    def _on_centered(self, **_: object) -> None:
        self._bridge.centered.emit()

    def _on_aborted(self) -> None:
        self._bridge.aborted.emit()

    # Main-thread slots -----------------------------------------------------

    def _show_countdown(self) -> None:
        self._start_pulse()
        self._guarded_show()

    def _show_centered(self) -> None:
        # Stop pulsing and keep the target visible steadily.  It stays until
        # the user moves/clicks/scrolls (→ mouse.centering_aborted).
        self._stop_pulse()
        self._guarded_show()

    def _do_aborted(self) -> None:
        self._stop_pulse()
        self.hide()


class PrecisionIndicator(CursorIndicator):
    """Shows 🐌 while precision (slow) mode is active."""

    _SYMBOL = "\U0001F40C"   # 🐌

    def __init__(self) -> None:
        super().__init__(self._SYMBOL, config_key="precision")
        bus.subscribe("mouse.precision_changed", self._on_precision)

    def _on_precision(self, enabled: bool, **_: object) -> None:
        if enabled:
            self.show_indicator()
        else:
            self.hide_indicator()

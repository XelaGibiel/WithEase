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
import logging
import os
from ctypes import wintypes

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import QWidget

from withease.core.event_bus import bus

_SIZE = 28
_GAP = 2

# Opt-in overlay diagnostics: set WITHEASE_DEBUG_OVERLAY=1 to log why a cursor
# symbol (e.g. the centering target) does or does not appear.  Off by default.
_DEBUG_OVERLAY = bool(os.environ.get("WITHEASE_DEBUG_OVERLAY"))
_log = logging.getLogger(__name__)


def _cursor_offset() -> int:
    """Offset in pixels that clears the current system cursor."""
    try:
        size = ctypes.windll.user32.GetSystemMetrics(13)  # SM_CXCURSOR
        return max(24, size + 4)
    except Exception:
        return 32


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def foreground_is_fullscreen() -> bool:
    """True if the focused window covers its entire monitor (fullscreen video,
    game, presentation …).  Our always-on-top cursor symbols must not draw over
    that – they are hidden while such a window is in front.

    Comparing the window rect to its monitor works regardless of DPI awareness
    (both rects are in the same coordinate space)."""
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd or hwnd in (u.GetDesktopWindow(), u.GetShellWindow()):
            return False
        # A window with a title bar (WS_CAPTION) is a normal/maximized window,
        # never a fullscreen video or game – so the cursor symbols must stay
        # visible over it.  Real fullscreen apps drop the caption (borderless).
        _GWL_STYLE = -16
        _WS_CAPTION = 0x00C00000
        style = u.GetWindowLongW(hwnd, _GWL_STYLE) & 0xFFFFFFFF
        if style & _WS_CAPTION == _WS_CAPTION:
            return False
        rect = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rect))
        mon = u.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        u.GetMonitorInfoW(mon, ctypes.byref(info))
        m = info.rcMonitor
        return (rect.left <= m.left and rect.top <= m.top
                and rect.right >= m.right and rect.bottom >= m.bottom)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Coordinator – keeps all registered indicators side by side
# ---------------------------------------------------------------------------

class IndicatorCoordinator:
    """Singleton that repositions all visible indicators next to the cursor."""

    _instance: IndicatorCoordinator | None = None

    def __init__(self) -> None:
        self._indicators: list[CursorIndicator] = []
        # Fixed overlays (sticky-keys / macro chips) that should also hide over
        # a fullscreen window but are NOT repositioned to the cursor.  Any
        # object with a set_suppressed(bool) method can register.
        self._suppressibles: list[object] = []
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

    def register_suppressible(self, widget: object) -> None:
        """Register a fixed overlay to be hidden while a fullscreen window is
        in front (must provide set_suppressed(bool))."""
        self._suppressibles.append(widget)

    def _reposition(self) -> None:
        # Hide every cursor symbol AND registered fixed overlay while a
        # fullscreen window is in front (video/game) – they keep their logical
        # state and reappear when the fullscreen window is left.
        fullscreen = foreground_is_fullscreen()
        if _DEBUG_OVERLAY and fullscreen != getattr(self, "_last_fullscreen", None):
            self._last_fullscreen = fullscreen
            _log.info("coordinator: fullscreen-in-front = %s (symbols %s)",
                      fullscreen, "hidden" if fullscreen else "shown")
        for ind in self._indicators:
            ind.set_suppressed(fullscreen)
        for w in self._suppressibles:
            w.set_suppressed(fullscreen)
        if fullscreen:
            return

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
        self._show_enabled = True      # user setting (per-symbol on/off)
        self._logical_visible = False  # the feature wants it shown
        self._suppressed = False       # temporarily hidden (fullscreen)
        self._config_key = config_key

        self._bridge = _Bridge()
        self._bridge.show_requested.connect(self._do_show)
        self._bridge.hide_requested.connect(self._do_hide)
        self._bridge.config_changed.connect(self._apply_config)

        # NOTE: the native window is created lazily (on first show / prewarm).
        # Creating it eagerly here is unreliable on a cold Windows autostart –
        # a translucent always-on-top window built before the desktop/DWM is
        # ready can stay invisible.  app.py calls prewarm() a few seconds after
        # start (desktop ready) to realise the window off-screen instead.

        if config_key is not None:
            bus.subscribe("mouse.indicator_config", self._on_config)

        IndicatorCoordinator.get().register(self)

    # Thread-safe API -------------------------------------------------------

    def show_indicator(self) -> None:
        self._bridge.show_requested.emit()

    def hide_indicator(self) -> None:
        self._bridge.hide_requested.emit()

    def prewarm(self) -> None:
        """Realise the native window off-screen once the desktop is ready.

        Called by app.py a few seconds after start.  Building the translucent
        always-on-top window now (rather than during a cold autostart) makes
        the first real show() reliable – otherwise the target symbol can stay
        invisible after a Windows restart.  Skipped while the symbol is
        actually shown, so it never disturbs a live indicator."""
        if self._logical_visible:
            return
        self.setWindowOpacity(0.0)
        self.move(-32000, -32000)
        self.show()
        self.hide()
        self.setWindowOpacity(1.0)

    # Config (show/hide this symbol) ---------------------------------------

    def _on_config(self, **flags: object) -> None:
        self._bridge.config_changed.emit(bool(flags.get(self._config_key, True)))

    def _apply_config(self, enabled: bool) -> None:
        self._show_enabled = enabled
        self._apply()

    def set_suppressed(self, suppressed: bool) -> None:
        """Temporarily hide/show without changing the logical state (used by
        the coordinator to hide symbols over a fullscreen window)."""
        if suppressed != self._suppressed:
            self._suppressed = suppressed
            self._apply()

    def _apply(self) -> None:
        """Actual visibility = feature wants it AND user allows it AND not
        currently suppressed by a fullscreen window."""
        should = (self._logical_visible and self._show_enabled
                  and not self._suppressed)
        self.setVisible(should)
        if should:
            # Place at the cursor immediately (don't wait for the coordinator's
            # next 16 ms tick) and force on top.  This covers the case where the
            # window was prewarmed off-screen (at -32000) or is hidden behind
            # another top-most window – the classic "target symbol never
            # appears" problem after a cold start / display change.
            offset = _cursor_offset()
            pos = QCursor.pos()
            self.move(pos.x() + offset, pos.y() + offset)
            self.raise_()
        if _DEBUG_OVERLAY:
            _log.info("indicator %r: logical=%s enabled=%s suppressed=%s "
                      "-> visible=%s at %s",
                      self._symbol, self._logical_visible, self._show_enabled,
                      self._suppressed, self.isVisible(),
                      (self.x(), self.y()))

    def _guarded_show(self) -> None:
        """Mark the symbol as logically shown (respects the gates in _apply)."""
        self._logical_visible = True
        self._apply()

    # Main-thread slots -----------------------------------------------------

    def _do_show(self) -> None:
        self._guarded_show()

    def _do_hide(self) -> None:
        self._logical_visible = False
        self._apply()

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
        self._do_hide()


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

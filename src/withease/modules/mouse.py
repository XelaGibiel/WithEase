"""Mouse Module.

Features:
- Automatic cursor centering after inactivity (configurable delay)
- Countdown tooltip before centering (abortable by movement or key press)
- Configurable center tolerance (won't center if already close enough)
- Manual centering via hotkey (registered in ActionManager)
- Precision mode (slow cursor speed via pynput mouse control)
- Click-Lock (hold left button without physical press)
- Keyboard keys as left / right / double click
- Screen zones: jump cursor to predefined screen regions via hotkey
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from typing import Any

from PySide6.QtWidgets import QWidget

from withease.core.action_manager import Action, action_manager
from withease.core.event_bus import bus
from withease.core.i18n import tr
from withease.core.keyboard_hook import (
    current_combo_str,
    is_altgr_fake_lctrl,
    shared_keyboard_hook,
    vk_to_combo_str,
)
from withease.modules.base import BaseModule

try:
    from pynput import mouse as pynput_mouse
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False


def _screen_size() -> tuple[int, int]:
    """Physical primary-screen size in pixels.

    On Windows this is DPI-aware via Win32 so cursor positions land correctly
    under display scaling; on other platforms it falls back to Qt's screen
    geometry."""
    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    try:
        from PySide6.QtGui import QGuiApplication
        geo = QGuiApplication.primaryScreen().geometry()
        return int(geo.width()), int(geo.height())
    except Exception:
        return (1920, 1080)


class MouseModule(BaseModule):
    MODULE_ID = "mouse"
    DESCRIPTION = "Maussteuerung und Cursor-Assistenz"

    @property
    def DISPLAY_NAME(self) -> str:  # type: ignore[override]
        return tr("module.mouse.name")

    # Windows API constants for mouse speed
    _SPI_GETMOUSESPEED = 0x0070
    _SPI_SETMOUSESPEED = 0x0071

    def __init__(self) -> None:
        super().__init__()
        self._settings: dict[str, Any] = {}
        self._mouse_listener: Any = None
        self._kb_subscribed = False
        # Centering runs on a polling thread that watches GetLastInputInfo
        # (system-wide last input time).  This is hook-independent, so it can
        # never be starved by low-level hooks the way a pynput mouse listener
        # can – movement, clicks, wheel and keys all reset it uniformly.
        self._centering_thread: threading.Thread | None = None
        self._centering_stop = threading.Event()
        self._symbol_shown = False       # centering target currently displayed
        self._center_pos = (0, 0)        # where the cursor was centred to
        self._original_mouse_speed: int | None = None

        action_manager.register(Action(
            id="mouse.center",
            label=tr("module.mouse.centering"),
            callback=self._center_cursor,
        ))
        action_manager.register(Action(
            id="mouse.precision_toggle",
            label=tr("module.mouse.precision"),
            callback=self._toggle_precision,
        ))
        action_manager.register(Action(
            id="mouse.click_lock_toggle",
            label=tr("module.mouse.click_lock"),
            callback=self._toggle_click_lock,
        ))
        action_manager.register(Action(
            id="mouse.highlight",
            label=tr("module.mouse.highlight"),
            callback=self._highlight_cursor,
        ))

        # Screen zone actions – up to 3×3 = 9 zones (numbered left-to-right, top-to-bottom)
        for zone_num in range(1, 10):
            action_manager.register(Action(
                id=f"mouse.zone_{zone_num}",
                label=tr("module.mouse.screen_zones.zone", num=str(zone_num)),
                callback=lambda n=zone_num: self._jump_to_zone_num(n),
            ))

    # ------------------------------------------------------------------
    # BaseModule interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        if PYNPUT_AVAILABLE:
            # Mouse listener is only needed to cancel Click-Lock on a physical
            # left click; centering activity is detected via GetLastInputInfo.
            self._mouse_listener = pynput_mouse.Listener(
                on_click=self._on_mouse_click,
            )
            self._mouse_listener.start()

        # Keyboard hotkeys via the shared low-level hook (pynput's keyboard
        # listener breaks AltGr/dead keys in the foreground app).
        shared_keyboard_hook.subscribe(self._on_key_event)
        self._kb_subscribed = True

        self._start_centering_loop()
        bus.publish("module.started", module_id=self.MODULE_ID)

    def stop(self) -> None:
        self._stop_centering_loop()

        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._kb_subscribed:
            shared_keyboard_hook.unsubscribe(self._on_key_event)
            self._kb_subscribed = False

        # Hide the centering target if it is currently shown.
        if self._symbol_shown:
            self._symbol_shown = False
            bus.publish("mouse.centering_aborted")

        # Always restore mouse speed if precision mode was active.  Going via
        # _disable_precision also hides the 🐌 indicator.
        self._disable_precision()
        self._settings["precision_mode_enabled"] = False

        # Release click-lock if it was active (also hides the 🔒 indicator).
        if self._settings.get("_click_lock_active", False):
            self._toggle_click_lock()
        self._settings["_click_lock_active"] = False

        bus.publish("module.stopped", module_id=self.MODULE_ID)

    def get_settings_widget(self) -> QWidget:
        from withease.gui.settings.mouse_settings import MouseSettingsWidget
        return MouseSettingsWidget(self)

    def on_settings_changed(self) -> None:
        """Called by the settings UI when any value changes.

        A tool's hotkey is only assigned while that tool is enabled – a disabled
        tool must not respond to its hotkey.
        """
        def trigger(enabled_key: str, hotkey_key: str) -> str:
            return (self._settings.get(hotkey_key, "")
                    if self._settings.get(enabled_key, False) else "")

        action_manager.assign_trigger(
            "mouse.center", trigger("centering_enabled", "centering_hotkey"))
        action_manager.assign_trigger(
            "mouse.precision_toggle",
            trigger("precision_mode_enabled", "precision_hotkey"))
        action_manager.assign_trigger(
            "mouse.click_lock_toggle",
            trigger("click_lock_enabled", "clicklock_hotkey"))
        action_manager.assign_trigger(
            "mouse.highlight", trigger("highlight_enabled", "highlight_hotkey"))

        zones_on = self._settings.get("screen_zones_enabled", False)
        rows, cols = self._get_grid()
        total = rows * cols
        for zone_num in range(1, 10):
            action_manager.assign_trigger(
                f"mouse.zone_{zone_num}",
                self._settings.get(f"screen_zone_{zone_num}_hotkey", "")
                if zones_on and zone_num <= total else "",
            )
        self._publish_indicator_config()
        bus.publish("module.settings_changed", module_id=self.MODULE_ID)

    def _publish_indicator_config(self) -> None:
        """Tell the cursor-symbol overlays which of them the user wants shown
        (the feature keeps working either way – only the symbol is hidden)."""
        bus.publish(
            "mouse.indicator_config",
            centering=bool(self._settings.get("centering_show_indicator", True)),
            precision=bool(self._settings.get("precision_show_indicator", True)),
            click_lock=bool(
                self._settings.get("click_lock_show_indicator", True)),
        )

    def load_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self.on_settings_changed()

    def dump_settings(self) -> dict[str, Any]:
        return self._settings

    # ------------------------------------------------------------------
    # Centering
    # ------------------------------------------------------------------

    def _on_mouse_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        """Cancel Click-Lock on a physical left click."""
        if pressed and button == pynput_mouse.Button.left \
                and self._settings.get("_click_lock_active", False):
            self._toggle_click_lock()

    def _on_key_event(self, vk: int, scan: int, extended: bool,
                      injected: bool, is_press: bool) -> bool:
        """Low-level hook callback.  Never suppresses (returns False).

        Runs in the hook thread's message loop – keep it quick.
        """
        if injected:
            return False  # ignore synthetic keys (our own click injection etc.)
        if is_altgr_fake_lctrl(vk, scan):
            return False
        if is_press:
            # Full combo including held modifiers, so hotkeys like
            # "shift+alt+'m'" (e.g. from a macro pad) match too.
            self._handle_key_press(current_combo_str(vk))
        else:
            self._handle_key_release(vk_to_combo_str(vk))
        return False

    def _handle_key_press(self, combo: str | None) -> None:
        """Handle hotkeys on press, reset centering timer."""
        if combo is None:
            return
        # Precision mode
        precision_key = self._settings.get("precision_hotkey", "")
        if (precision_key and combo == precision_key
                and self._settings.get("precision_mode_enabled", False)):
            mode = self._settings.get("precision_mode_type", "hold")
            if mode == "hold":
                self._enable_precision()
            else:
                self._toggle_precision()
            return

        # Keyboard as mouse buttons
        if self._settings.get("keyboard_clicks_enabled", False) and PYNPUT_AVAILABLE:
            if combo == self._settings.get("keyboard_click_left", ""):
                self._perform_click(pynput_mouse.Button.left)
                return
            if combo == self._settings.get("keyboard_click_right", ""):
                self._perform_click(pynput_mouse.Button.right)
                return
            if combo == self._settings.get("keyboard_click_double", ""):
                self._perform_double_click()
                return

        # All other registered actions (e.g. centering).  Keyboard activity
        # resets the centering timer automatically via GetLastInputInfo.
        action_manager.fire(combo)

    def _handle_key_release(self, key_str: str | None) -> None:
        """Disable precision mode on key release (hold mode only).

        Compares against the MAIN key of the hotkey: the release of "M" ends
        hold mode for "shift+alt+'m'" even if the modifiers are still down.
        """
        precision_key = self._settings.get("precision_hotkey", "")
        if (key_str and precision_key
                and key_str == precision_key.split("+")[-1]
                and self._settings.get("precision_mode_enabled", False)
                and self._settings.get("precision_mode_type", "hold") == "hold"):
            self._disable_precision()

    # -- Activity detection via GetLastInputInfo (hook-independent) ---------

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    _CENTER_TOL = 4  # px; cursor must move this far from centre to dismiss target

    def _now_ms(self) -> int:
        """Monotonic millisecond tick in the same timebase as _last_input_tick."""
        if sys.platform == "win32":
            return int(ctypes.windll.kernel32.GetTickCount())
        return int(time.monotonic() * 1000)

    def _last_input_tick(self) -> int:
        """Tick (ms) of the last user input.

        On Windows this is the OS-wide GetLastInputInfo (keyboard AND mouse).
        On other platforms there is no portable system-idle API without extra
        dependencies, so we approximate it by watching for cursor movement –
        keyboard-only activity does not reset the idle timer there (Linux
        limitation; centring still works while the mouse is stationary)."""
        if sys.platform == "win32":
            info = self._LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(info)
            ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
            return int(info.dwTime)
        pos = self._cursor_pos()
        now = self._now_ms()
        if getattr(self, "_li_pos", None) != pos:
            self._li_pos = pos
            self._li_ms = now
        return int(getattr(self, "_li_ms", now))

    def _cursor_pos(self) -> tuple[int, int]:
        if sys.platform == "win32":
            pt = self._POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            return (int(pt.x), int(pt.y))
        try:
            x, y = pynput_mouse.Controller().position
            return (int(x), int(y))
        except Exception:
            return (0, 0)

    def _start_centering_loop(self) -> None:
        self._stop_centering_loop()
        self._centering_stop.clear()
        self._centering_thread = threading.Thread(
            target=self._centering_loop, daemon=True)
        self._centering_thread.start()

    def _stop_centering_loop(self) -> None:
        self._centering_stop.set()
        self._centering_thread = None

    def _centering_loop(self) -> None:
        """Idle-watch loop: after `delay` of no input, run a countdown then
        centre the cursor.  Any input during the wait cancels it.  The target
        symbol stays visible after centring (manual hotkey or automatic) until
        the next input."""
        last_seen = self._last_input_tick()
        stop = self._centering_stop

        while not stop.is_set():
            if not (self._enabled and self._settings.get("centering_enabled")):
                if self._symbol_shown:
                    bus.publish("mouse.centering_aborted")
                    self._symbol_shown = False
                if stop.wait(0.2):
                    break
                continue

            if self._symbol_shown:
                # After centering the target stays put until the CURSOR is
                # moved away from the centre.  We watch the position (not input
                # events) so releasing the trigger key doesn't dismiss it.
                x, y = self._cursor_pos()
                cx, cy = self._center_pos
                if abs(x - cx) > self._CENTER_TOL or abs(y - cy) > self._CENTER_TOL:
                    bus.publish("mouse.centering_aborted")
                    self._symbol_shown = False
                    last_seen = self._last_input_tick()
                if stop.wait(0.05):
                    break
                continue

            tick = self._last_input_tick()
            if tick != last_seen:
                last_seen = tick  # any activity resets the idle timer
                if stop.wait(0.05):
                    break
                continue

            idle = (self._now_ms() - tick) / 1000.0
            delay = float(self._settings.get("centering_delay", 5.0))
            if idle < delay:
                if stop.wait(min(0.2, max(0.05, delay - idle))):
                    break
                continue

            # Idle long enough → countdown, then centre (abort on any input).
            countdown = int(self._settings.get("centering_countdown", 3))
            if countdown > 0:
                bus.publish("mouse.centering_countdown", seconds=countdown)
                end = time.monotonic() + countdown
                aborted = False
                while time.monotonic() < end:
                    if stop.wait(0.05):
                        return
                    if self._last_input_tick() != last_seen:
                        aborted = True
                        break
                if aborted:
                    last_seen = self._last_input_tick()
                    bus.publish("mouse.centering_aborted")
                    continue

            if self._enabled and self._settings.get("centering_enabled"):
                self._center_cursor()  # sets _symbol_shown + _center_pos

    def _center_cursor(self) -> None:
        if not PYNPUT_AVAILABLE:
            return
        try:
            cx, cy = _screen_size()
            cx //= 2
            cy //= 2
            pynput_mouse.Controller().position = (cx, cy)
            # Read back the ACTUAL cursor position as the reference point (pynput
            # and GetCursorPos can disagree slightly under DPI scaling).  The
            # loop keeps the target visible until the cursor leaves this point.
            self._center_pos = self._cursor_pos()
            self._symbol_shown = True
            bus.publish("mouse.centered")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Precision mode
    # ------------------------------------------------------------------

    def _get_system_mouse_speed(self) -> int:
        # Precision mode changes the OS pointer speed; there is no portable
        # equivalent, so on non-Windows it is a no-op (returns the neutral 10).
        if sys.platform != "win32":
            return 10
        speed = ctypes.c_int(0)
        ctypes.windll.user32.SystemParametersInfoW(
            self._SPI_GETMOUSESPEED, 0, ctypes.byref(speed), 0)
        return speed.value

    def _set_system_mouse_speed(self, speed: int) -> None:
        if sys.platform != "win32":
            return
        ctypes.windll.user32.SystemParametersInfoW(
            self._SPI_SETMOUSESPEED, 0, speed, 0)

    def _enable_precision(self) -> None:
        if self._original_mouse_speed is not None:
            return  # already active
        self._original_mouse_speed = self._get_system_mouse_speed()
        precision = int(self._settings.get("precision_speed", 3))
        slow_speed = max(1, round(precision / 2))
        self._set_system_mouse_speed(slow_speed)
        bus.publish("mouse.precision_changed", enabled=True)

    def _disable_precision(self) -> None:
        if self._original_mouse_speed is None:
            return  # already inactive
        self._set_system_mouse_speed(self._original_mouse_speed)
        self._original_mouse_speed = None
        bus.publish("mouse.precision_changed", enabled=False)

    def _toggle_precision(self) -> None:
        if self._original_mouse_speed is None:
            self._enable_precision()
        else:
            self._disable_precision()

    # ------------------------------------------------------------------
    # Screen zones
    # ------------------------------------------------------------------

    _GRIDS: dict[str, tuple[int, int]] = {"1x2": (1, 2), "2x2": (2, 2), "3x3": (3, 3)}

    def _get_grid(self) -> tuple[int, int]:
        return self._GRIDS.get(self._settings.get("screen_zones_grid", "3x3"), (3, 3))

    def _jump_to_zone_num(self, zone_num: int) -> None:
        rows, cols = self._get_grid()
        if zone_num > rows * cols:
            return
        idx = zone_num - 1
        self._jump_to_zone(idx // cols, idx % cols, rows, cols)

    def _jump_to_zone(self, row: int, col: int, rows: int, cols: int) -> None:
        """Move cursor to the centre of the given grid cell."""
        if not PYNPUT_AVAILABLE:
            return
        if not self._settings.get("screen_zones_enabled", False):
            return
        try:
            w, h = _screen_size()
            x = int(w * (2 * col + 1) / (2 * cols))
            y = int(h * (2 * row + 1) / (2 * rows))
            pynput_mouse.Controller().position = (x, y)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Keyboard as mouse buttons
    # ------------------------------------------------------------------

    def _perform_click(self, button: Any) -> None:
        try:
            ctrl = pynput_mouse.Controller()
            ctrl.press(button)
            ctrl.release(button)
        except Exception:
            pass

    def _perform_double_click(self) -> None:
        try:
            ctrl = pynput_mouse.Controller()
            ctrl.press(pynput_mouse.Button.left)
            ctrl.release(pynput_mouse.Button.left)
            ctrl.press(pynput_mouse.Button.left)
            ctrl.release(pynput_mouse.Button.left)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Cursor highlight
    # ------------------------------------------------------------------

    def _highlight_cursor(self) -> None:
        """Trigger the pulsing highlight overlay at the current cursor position."""
        rings = bool(self._settings.get("highlight_rings", True))
        ring_style = self._settings.get("highlight_ring_style", "open")
        color = self._settings.get("highlight_color", [255, 140, 0])
        radius = int(self._settings.get("highlight_radius", 90))
        arrow = bool(self._settings.get("highlight_arrow", False))
        arrow_thickness = int(self._settings.get("highlight_arrow_thickness", 6))
        duration_ms = int(float(self._settings.get("highlight_duration", 1.6))
                          * 1000)
        bus.publish("mouse.highlight", rings=rings, ring_style=ring_style,
                    color=color, radius=radius,
                    arrow=arrow, arrow_thickness=arrow_thickness,
                    duration_ms=duration_ms)

    # ------------------------------------------------------------------
    # Click-Lock
    # ------------------------------------------------------------------

    def _toggle_click_lock(self) -> None:
        if not PYNPUT_AVAILABLE:
            return
        try:
            ctrl = pynput_mouse.Controller()
            if not self._settings.get("_click_lock_active", False):
                ctrl.press(pynput_mouse.Button.left)
                self._settings["_click_lock_active"] = True
            else:
                ctrl.release(pynput_mouse.Button.left)
                self._settings["_click_lock_active"] = False
            bus.publish("mouse.click_lock_changed",
                        enabled=self._settings["_click_lock_active"])
        except Exception:
            pass

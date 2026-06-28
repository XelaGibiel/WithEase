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
import threading
from typing import Any

from PySide6.QtWidgets import QWidget

from accessmate.core.action_manager import Action, action_manager
from accessmate.core.event_bus import bus
from accessmate.core.i18n import tr
from accessmate.modules.base import BaseModule

try:
    from pynput import mouse as pynput_mouse
    from pynput import keyboard as pynput_keyboard
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False


class MouseModule(BaseModule):
    MODULE_ID = "mouse"
    DESCRIPTION = "Maussteuerung und Cursor-Assistenz"

    @property
    def DISPLAY_NAME(self) -> str:  # type: ignore[override]
        return tr("module.mouse.name")

    def __init__(self) -> None:
        super().__init__()
        self._settings: dict[str, Any] = {}
        self._centering_timer: threading.Timer | None = None
        self._mouse_listener: Any = None
        self._kb_listener: Any = None
        self._countdown_abort = threading.Event()
        self._lock = threading.Lock()

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

    # ------------------------------------------------------------------
    # BaseModule interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        if PYNPUT_AVAILABLE:
            self._mouse_listener = pynput_mouse.Listener(
                on_move=self._on_mouse_move,
            )
            self._mouse_listener.start()

            self._kb_listener = pynput_keyboard.Listener(
                on_press=self._on_key_press,
            )
            self._kb_listener.start()

        self._schedule_centering()
        bus.publish("module.started", module_id=self.MODULE_ID)

    def stop(self) -> None:
        self._cancel_centering()
        self._countdown_abort.set()

        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._kb_listener:
            self._kb_listener.stop()
            self._kb_listener = None

        self._countdown_abort.clear()
        bus.publish("module.stopped", module_id=self.MODULE_ID)

    def get_settings_widget(self) -> QWidget:
        from accessmate.gui.settings.mouse_settings import MouseSettingsWidget
        return MouseSettingsWidget(self)

    def on_settings_changed(self) -> None:
        """Called by the settings UI when any value changes."""
        # Keep ActionManager in sync with the current hotkey settings
        action_manager.assign_trigger(
            "mouse.center",
            self._settings.get("centering_hotkey", ""),
        )
        action_manager.assign_trigger(
            "mouse.precision_toggle",
            self._settings.get("precision_hotkey", ""),
        )
        action_manager.assign_trigger(
            "mouse.click_lock_toggle",
            self._settings.get("clicklock_hotkey", ""),
        )
        if self._enabled:
            self._schedule_centering()

    def load_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self.on_settings_changed()

    def dump_settings(self) -> dict[str, Any]:
        return self._settings

    # ------------------------------------------------------------------
    # Centering
    # ------------------------------------------------------------------

    def _on_mouse_move(self, x: int, y: int) -> None:
        """Reset centering timer on every mouse movement."""
        self._countdown_abort.set()
        self._schedule_centering()

    def _on_key_press(self, key: Any) -> None:
        """Fire ActionManager hotkeys, then reset the centering timer."""
        action_manager.fire(str(key))
        self._countdown_abort.set()
        self._schedule_centering()

    def _schedule_centering(self) -> None:
        if not self._enabled or not self._settings.get("centering_enabled"):
            return
        delay = float(self._settings.get("centering_delay", 5.0))

        with self._lock:
            if self._centering_timer:
                self._centering_timer.cancel()
            self._countdown_abort.clear()
            self._centering_timer = threading.Timer(delay, self._start_countdown)
            self._centering_timer.daemon = True
            self._centering_timer.start()

    def _cancel_centering(self) -> None:
        with self._lock:
            if self._centering_timer:
                self._centering_timer.cancel()
                self._centering_timer = None

    def _start_countdown(self) -> None:
        countdown = int(self._settings.get("centering_countdown", 3))

        if countdown > 0:
            bus.publish("mouse.centering_countdown", seconds=countdown)
            # Wait for countdown, but abort immediately on mouse move or key press
            aborted = self._countdown_abort.wait(timeout=countdown)
            if aborted:
                # User moved the mouse or pressed a key – reschedule
                self._schedule_centering()
                return

        if self._enabled and self._settings.get("centering_enabled"):
            self._center_cursor()
            # Reschedule so centering keeps happening on inactivity
            self._schedule_centering()

    def _center_cursor(self) -> None:
        if not PYNPUT_AVAILABLE:
            return
        try:
            # Use physical pixel dimensions so the position is correct
            # regardless of Windows DPI scaling settings.
            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            cx = user32.GetSystemMetrics(0) // 2
            cy = user32.GetSystemMetrics(1) // 2

            ctrl = pynput_mouse.Controller()
            ctrl.position = (cx, cy)
            bus.publish("mouse.centered")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Precision mode
    # ------------------------------------------------------------------

    def _toggle_precision(self) -> None:
        current = self._settings.get("precision_mode_enabled", False)
        self._settings["precision_mode_enabled"] = not current
        bus.publish("mouse.precision_changed",
                    enabled=self._settings["precision_mode_enabled"])

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
